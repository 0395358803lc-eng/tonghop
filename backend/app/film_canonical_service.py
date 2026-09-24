import asyncio
import base64
import json
import os
from typing import Iterable

import httpx

from .film_resource_store import (
    get_project_resource,
    list_project_resources,
    save_canonical_asset,
    sync_project_resources,
    update_resource_binding,
)
from .film_media_service import register_canonical_from_resource
from .film_store import get_film_project, update_film_project
from .film_canonical_qc import _visual_bible, evaluate_canonical_asset
from .film_canonical_run import (
    bump_result,
    emit_resource_event,
    finish_run,
    mark_resource,
    mark_run_started,
    set_current_resource,
    stop_requested,
)
from .flow_bridge_client import render_flow_image
from .film_flow_errors import is_flow_dependency_error, render_error_code
from .provider_store import get_provider
from .providers.registry import PROVIDERS

DEFAULT_IMAGE_PROVIDER = os.getenv("FILM_CANONICAL_IMAGE_PROVIDER", "xkiro").strip() or "xkiro"
DEFAULT_IMAGE_MODEL = os.getenv("FILM_CANONICAL_IMAGE_MODEL", "gpt-image").strip() or "gpt-image"
DEFAULT_FLOW_IMAGE_MODEL = os.getenv("FILM_CANONICAL_FLOW_IMAGE_MODEL", "Nano Banana 2").strip() or "Nano Banana 2"
FREE_FALLBACK_IMAGE_MODEL = os.getenv("FILM_CANONICAL_FREE_IMAGE_MODEL", "sensenova/sensenova-u1.5-lite").strip() or "sensenova/sensenova-u1.5-lite"
POLL_TIMEOUT = max(60, min(int(os.getenv("FILM_CANONICAL_IMAGE_TIMEOUT", "300")), 900))
MAX_QC_REGENERATIONS = max(1, min(int(os.getenv("FILM_CANONICAL_QC_MAX_ATTEMPTS", "3")), 5))
_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}


def _project_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _PROJECT_LOCKS:
        _PROJECT_LOCKS[project_id] = asyncio.Lock()
    return _PROJECT_LOCKS[project_id]


def _publish_canonical_media(resource):
    if not resource:
        return resource
    try:
        register_canonical_from_resource(resource)
    except Exception:
        pass
    return resource
def _entity(project: dict, resource_type: str, entity_id: str) -> dict:
    source = {
        "character": project.get("characters") or [],
        "location": project.get("locations") or [],
        "prop": project.get("props") or [],
    }.get(resource_type, [])
    aliases = {
        "character": ("id", "character_id"),
        "location": ("id", "location_id"),
        "prop": ("id", "prop_id"),
    }[resource_type]
    for item in source:
        if str(item.get(aliases[0]) or item.get(aliases[1]) or "") == entity_id:
            return item
    raise ValueError(f"Không tìm thấy {resource_type}:{entity_id} trong Story Bible.")


def _size_for(resource_type: str) -> str:
    if resource_type == "location":
        return "1792x1024"
    return "1024x1024"


def _aspect_for(resource_type: str) -> str:
    return "16:9" if resource_type == "location" else "1:1"


def _prompt(project: dict, resource_type: str, entity_id: str, entity: dict) -> str:
    style = str(project.get("visual_style") or project.get("settings", {}).get("style") or "Cinematic")
    payload = json.dumps(_visual_bible(resource_type, entity), ensure_ascii=False, indent=2)
    common = (
        f"Create a canonical production reference image for TH Media. Asset ID: {entity_id}. "
        f"Film visual style: {style}. This image will be reused as a strict visual identity reference "
        "across many AI-generated video scenes, so prioritize stable, unambiguous visual details. "
        "Do not add captions, labels, watermarks, borders, split panels, or UI elements. "
    )
    if resource_type == "character":
        return common + (
            "Create ONE photorealistic cinematic character reference. Show the same character clearly, "
            "neutral expression, face unobstructed, natural anatomy, medium-to-full body framing, "
            "wardrobe and accessories exactly as described. Use a simple unobtrusive background and neutral "
            "lighting so face, hair, skin tone, clothing colors and signature traits are easy to compare. "
            "Do not invent extra accessories or alternate outfits. STORY BIBLE CHARACTER:\n" + payload
        )
    if resource_type == "location":
        return common + (
            "Create ONE cinematic establishing reference of the location with NO people. Preserve architecture, "
            "layout, doors, windows, fixed furniture, dominant colors, practical lights, time of day and weather "
            "exactly as described. Use a clear wide composition suitable for geometry continuity checks. "
            "Do not redesign or add unexplained structures. STORY BIBLE LOCATION:\n" + payload
        )
    return common + (
        "Create ONE clean cinematic reference image of the prop/object. Show the complete object clearly, "
        "with exact intrinsic shape, materials, color, wear and physical state described in the VISUAL BIBLE. "
        "Use a neutral uncluttered background, no hands and no people. Do NOT invent or encode scene placement, "
        "owner, left/right table position, transfer state or surrounding scene continuity; those are handled separately. "
        "Do not invent alternate versions. VISUAL BIBLE PROP:\n" + payload
    )


def _qc_rank(report: dict | None) -> tuple[int, float]:
    if not isinstance(report, dict):
        return (-1, -1.0)
    gate = report.get("hard_gate") if isinstance(report.get("hard_gate"), dict) else {}
    dimensions = gate.get("dimensions") if isinstance(gate.get("dimensions"), dict) else {}
    passed_dimensions = sum(1 for item in dimensions.values() if isinstance(item, dict) and item.get("passed") is True)
    try:
        overall = float(report.get("overall_score"))
    except Exception:
        overall = -1.0
    return (passed_dimensions, overall)


def _base_url(provider: str, configured: dict) -> str:
    configured_base = str(configured.get("base_url") or "").rstrip("/")
    if configured_base:
        return configured_base
    return str(PROVIDERS[provider]["base_url"]).rstrip("/")
async def _xkiro_generate(api_key: str, base_url: str, prompt: str, model: str, size: str) -> tuple[bytes, dict]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/images/generations",
            headers=headers,
            json={"model": model, "prompt": prompt, "n": 1, "size": size},
        )
        if response.status_code not in {200, 201, 202}:
            raise RuntimeError(f"IMAGE_CREATE_HTTP_{response.status_code}: {response.text[:1000]}")
        created = response.json()

    job_id = str(created.get("id") or "")
    if not job_id:
        raise RuntimeError("Image provider không trả job id.")

    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT
    wait_seconds = 2.0
    job = created
    while asyncio.get_running_loop().time() < deadline:
        status = str(job.get("status") or "").lower()
        if status == "succeeded":
            break
        if status in {"failed", "blocked", "cancelled", "canceled"}:
            error = job.get("error")
            raise RuntimeError(f"IMAGE_{status.upper()}: {json.dumps(error, ensure_ascii=False)[:1200]}")
        await asyncio.sleep(wait_seconds)
        wait_seconds = min(wait_seconds * 1.4, 8.0)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/images/generations/{job_id}", headers=headers)
            response.raise_for_status()
            job = response.json()
    if str(job.get("status") or "").lower() != "succeeded":
        raise RuntimeError(f"IMAGE_GENERATION_TIMEOUT: {job_id}")

    data = job.get("data") or []
    image_url = str(data[0].get("url") or "") if data and isinstance(data[0], dict) else ""
    if not image_url:
        raise RuntimeError("Image job hoàn tất nhưng không có URL ảnh.")

    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        image_response = await client.get(image_url)
        image_response.raise_for_status()
        image_bytes = image_response.content
    if not image_bytes:
        raise RuntimeError("Tải ảnh canonical từ CDN thất bại.")

    return image_bytes, {
        "provider": "xkiro",
        "model": model,
        "provider_job_id": job_id,
        "source_url": image_url,
        "provider_status": job.get("status"),
        "aspect_ratio": job.get("aspect_ratio"),
    }


async def _flow_generate(
    project: dict,
    project_id: str,
    resource_type: str,
    entity_id: str,
    prompt: str,
    model: str,
    asset_version: int,
    attempt: int,
    run_id: str | None = None,
) -> tuple[bytes, dict]:
    settings = project.get("settings") or {}
    flow_project_id = settings.get("flow_project_id")
    payload = {
        "project_id": project_id,
        "flow_project_id": flow_project_id,
        "asset_id": f"{resource_type}:{entity_id}",
        "idempotency_key": f"canonical:{project_id}:{resource_type}:{entity_id}:v{asset_version}:a{attempt}:flow:{model}",
        "prompt": prompt,
        "model": model,
        "aspect_ratio": _aspect_for(resource_type),
        "output_count": 1,
    }
    last_stage = {"value": None}

    def on_progress(stage: str, job: dict) -> None:
        normalized = {
            "submitted": "opening_flow_project",
            "queued": "opening_flow_project",
            "preparing": "opening_flow_project",
            "generating": "awaiting_generation",
            "completed": "downloading_result",
            "succeeded": "downloading_result",
            "success": "downloading_result",
            "downloading_result": "downloading_result",
            "downloaded": "downloaded",
        }.get(str(stage or "").lower(), "awaiting_generation")
        progress = job.get("progress") if isinstance(job, dict) else None
        stage_key = (normalized, progress)
        if last_stage["value"] == stage_key:
            return
        last_stage["value"] = stage_key
        provider_job_id = str((job or {}).get("job_id") or (job or {}).get("id") or "") or None
        message = {
            "opening_flow_project": "Flow đã nhận job; đang mở project và chuẩn bị cấu hình tạo ảnh.",
            "awaiting_generation": "Google Flow đang tạo ảnh.",
            "downloading_result": "Ảnh đã tạo xong; đang tải kết quả về TH Media.",
            "downloaded": "Đã tải ảnh từ Google Flow về TH Media.",
        }.get(normalized, "Google Flow đang xử lý ảnh.")
        mark_resource(
            project_id, resource_type, entity_id, normalized,
            run_id=run_id, status="pending", error=None,
            metadata_patch={
                "provider_job_id": provider_job_id,
                "provider_stage": str(stage or ""),
                "provider_progress": progress,
            },
        )
        emit_resource_event(
            project_id, run_id, "CANONICAL_RESOURCE_PROGRESS", resource_type, entity_id,
            message=message,
            payload={
                "stage": normalized,
                "provider_stage": str(stage or ""),
                "progress": progress,
                "provider_job_id": provider_job_id,
            },
        )

    result = await render_flow_image(payload, on_progress=on_progress)
    image_bytes = result.get("image_bytes") or b""
    if not image_bytes:
        raise RuntimeError("Google Flow hoàn tất nhưng không trả dữ liệu ảnh.")

    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    effective = raw.get("effective_settings") if isinstance(raw.get("effective_settings"), dict) else {}
    resolved_flow_project_id = str(result.get("flow_project_id") or "").strip() or None
    if resolved_flow_project_id and resolved_flow_project_id != flow_project_id:
        settings["flow_project_id"] = resolved_flow_project_id
        project["settings"] = settings
        update_film_project(project_id, settings=settings)

    return image_bytes, {
        "provider": "flow",
        "model": result.get("model") or model,
        "requested_model": model,
        "provider_job_id": result.get("provider_job_id"),
        "source_url": result.get("source_url"),
        "flow_project_id": resolved_flow_project_id,
        "flow_media_id": result.get("media_id"),
        "aspect_ratio": result.get("aspect_ratio") or _aspect_for(resource_type),
        "flow_project_recovered": bool(effective.get("project_recovered")),
        "recovered_from_flow_project_id": effective.get("recovered_from_project_id"),
        "fallback_used": False,
    }


class CanonicalGenerationStopped(RuntimeError):
    pass


async def generate_canonical_resource(
    project_id: str,
    resource_type: str,
    entity_id: str,
    *,
    provider: str = DEFAULT_IMAGE_PROVIDER,
    model: str = DEFAULT_IMAGE_MODEL,
    max_qc_attempts: int = MAX_QC_REGENERATIONS,
    run_id: str | None = None,
) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    if resource_type not in {"character", "location", "prop"}:
        raise ValueError("Resource type không hợp lệ.")

    resource = get_project_resource(project_id, resource_type, entity_id, "flow")
    if not resource:
        sync_project_resources(project_id, "flow")
        resource = get_project_resource(project_id, resource_type, entity_id, "flow")
    if not resource:
        raise ValueError("Không tìm thấy resource record.")
    if resource.get("status") == "locked":
        return resource

    provider = str(provider or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider not in {"xkiro", "flow"}:
        raise ValueError(f"Provider tạo ảnh {provider} chưa được adapter Canonical Asset hỗ trợ.")
    if provider == "flow" and (not model or model == DEFAULT_IMAGE_MODEL):
        model = DEFAULT_FLOW_IMAGE_MODEL

    credentials = None
    if provider == "xkiro":
        credentials = get_provider(provider)
        if not credentials:
            raise ValueError("Provider tạo ảnh xKiro chưa được cấu hình.")

    entity = _entity(project, resource_type, entity_id)
    base_prompt = _prompt(project, resource_type, entity_id, entity)
    metadata = dict(resource.get("metadata") or {})
    base_attempt = int(metadata.get("generation_attempt") or 0)
    qc_history = list(metadata.get("canonical_qc_history") or [])
    repair_feedback = str(metadata.get("canonical_repair_feedback") or "").strip()
    best_candidate = metadata.get("canonical_best_candidate")
    if not isinstance(best_candidate, dict):
        best_candidate = None
    current_qc = metadata.get("canonical_qc") if isinstance(metadata.get("canonical_qc"), dict) else None
    if resource.get("local_path") and current_qc:
        current_candidate = {
            "local_path": str(resource.get("local_path") or ""),
            "canonical_filename": metadata.get("canonical_filename"),
            "canonical_sha256": metadata.get("canonical_sha256"),
            "source_asset_version": metadata.get("asset_version"),
            "qc": current_qc,
        }
        if not best_candidate or _qc_rank(current_qc) > _qc_rank(best_candidate.get("qc")):
            best_candidate = current_candidate

    for retry_index in range(max(1, max_qc_attempts)):
        if run_id and stop_requested(project_id, run_id):
            mark_resource(project_id, resource_type, entity_id, "stopped", run_id=run_id, status="pending", error=None)
            emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_STOPPED", resource_type, entity_id,
                                severity="WARN", message="Đã dừng trước khi bắt đầu lần tạo tiếp theo.")
            raise CanonicalGenerationStopped("STOPPED_BY_USER")
        attempt = base_attempt + retry_index + 1
        prompt = base_prompt
        if repair_feedback:
            prompt += (
                "\n\nMANDATORY QC REPAIR FOR THIS RETRY:\n"
                + repair_feedback
                + "\nPreserve every Story Bible attribute that was already correct. "
                  "Do not redesign the subject; only repair the failed dimensions."
            )

        update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider="flow",
            status="pending",
            error=None,
            metadata_patch={
                "generation_status": "opening_flow_project" if provider == "flow" else "generating",
                "generation_provider": provider,
                "generation_model": model,
                "generation_prompt": prompt,
                "generation_attempt": attempt,
                "canonical_qc_status": "pending",
                "visual_lock": "QC_PENDING",
            },
        )

        try:
            current = get_project_resource(project_id, resource_type, entity_id, "flow") or resource
            actual_model = model
            fallback_used = False

            if provider == "flow":
                next_version = int((current.get("metadata") or {}).get("asset_version") or 0) + 1
                image_bytes, provenance = await _flow_generate(
                    project,
                    project_id,
                    resource_type,
                    entity_id,
                    prompt,
                    actual_model,
                    next_version,
                    attempt,
                    run_id=run_id,
                )
            else:
                try:
                    image_bytes, provenance = await _xkiro_generate(
                        credentials["api_key"],
                        _base_url(provider, credentials),
                        prompt,
                        actual_model,
                        _size_for(resource_type),
                    )
                except Exception as primary_exc:
                    message = str(primary_exc).lower()
                    paid_blocked = (
                        "paid model" in message
                        or "permission_denied" in message
                        or "free plan" in message
                    )
                    if actual_model != FREE_FALLBACK_IMAGE_MODEL and paid_blocked:
                        actual_model = FREE_FALLBACK_IMAGE_MODEL
                        fallback_used = True
                        image_bytes, provenance = await _xkiro_generate(
                            credentials["api_key"],
                            _base_url(provider, credentials),
                            prompt,
                            actual_model,
                            _size_for(resource_type),
                        )
                        provenance["fallback_reason"] = str(primary_exc)[:500]
                    else:
                        raise
                provenance["fallback_used"] = fallback_used
                provenance["requested_model"] = model
                provenance["model"] = actual_model

            encoded = base64.b64encode(image_bytes).decode("ascii")
            saved = save_canonical_asset(
                project_id,
                resource_type,
                entity_id,
                encoded,
                f"ai_{resource_type}_{entity_id}.png",
                "flow",
                status="pending",
            )

            flow_media_id = str(provenance.get("flow_media_id") or "").strip()
            if provider == "flow" and flow_media_id:
                saved = update_resource_binding(
                    project_id,
                    resource_type,
                    entity_id,
                    provider="flow",
                    provider_ref=flow_media_id,
                    metadata_patch={
                        "flow_asset_id": flow_media_id,
                        "flow_asset_project_id": provenance.get("flow_project_id"),
                        "provider_ref_kind": "flow_media_id",
                        "provider_ref_normalized": True,
                    },
                )

            emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_DOWNLOADED", resource_type, entity_id,
                                message="Ảnh đã được tải và lưu vào Media.", payload={"local_path": str(saved.get("local_path") or "")})
            if run_id and stop_requested(project_id, run_id):
                _publish_canonical_media(saved)
                mark_resource(project_id, resource_type, entity_id, "stopped", run_id=run_id, status="pending", error=None)
                emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_STOPPED", resource_type, entity_id,
                                    severity="WARN", message="Đã dừng sau khi lưu ảnh, trước bước QC.")
                raise CanonicalGenerationStopped("STOPPED_BY_USER")
            mark_resource(project_id, resource_type, entity_id, "qc_running", run_id=run_id, status="pending", error=None)
            emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_QC_STARTED", resource_type, entity_id,
                                message="Bắt đầu Canonical Vision QC.")
            qc_report = await evaluate_canonical_asset(
                project,
                resource_type,
                entity_id,
                entity,
                str(saved.get("local_path") or ""),
            )
            saved_meta = saved.get("metadata") or {}
            candidate = {
                "local_path": str(saved.get("local_path") or ""),
                "canonical_filename": saved_meta.get("canonical_filename"),
                "canonical_sha256": saved_meta.get("canonical_sha256"),
                "source_asset_version": saved_meta.get("asset_version"),
                "qc": qc_report,
            }
            if not best_candidate or _qc_rank(qc_report) > _qc_rank(best_candidate.get("qc")):
                best_candidate = candidate

            qc_history.append({
                "asset_version": saved_meta.get("asset_version"),
                "local_path": str(saved.get("local_path") or ""),
                "attempt": attempt,
                "passed": bool(qc_report.get("passed")),
                "overall_score": qc_report.get("overall_score"),
                "model": qc_report.get("model"),
                "hard_gate": qc_report.get("hard_gate"),
                "issues": qc_report.get("issues") or [],
            })
            qc_history = qc_history[-10:]

            if qc_report.get("passed") is True:
                emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_QC_PASSED", resource_type, entity_id,
                                    message="Canonical Vision QC đạt.", payload={"score": qc_report.get("overall_score")})
                return _publish_canonical_media(update_resource_binding(
                    project_id,
                    resource_type,
                    entity_id,
                    provider="flow",
                    status="ready",
                    error=None,
                    metadata_patch={
                        **provenance,
                        "generation_status": "succeeded",
                        "generated_by_ai": True,
                        "generation_prompt": prompt,
                        "generation_attempt": attempt,
                        "canonical_qc_status": "passed",
                        "canonical_qc": qc_report,
                        "canonical_qc_history": qc_history,
                        "canonical_best_candidate": best_candidate,
                        "visual_lock": "READY",
                    },
                ))

            repair_feedback = str(qc_report.get("repair_feedback") or "").strip()
            final_attempt = retry_index + 1 >= max(1, max_qc_attempts)
            failed_resource = update_resource_binding(
                project_id,
                resource_type,
                entity_id,
                provider="flow",
                status="error" if final_attempt else "pending",
                error=(
                    f"CANONICAL_QC_FAILED: score={qc_report.get('overall_score')}."
                    if final_attempt
                    else None
                ),
                metadata_patch={
                    **provenance,
                    "generation_status": "qc_failed" if final_attempt else "regenerating",
                    "generated_by_ai": True,
                    "generation_prompt": prompt,
                    "generation_attempt": attempt,
                    "canonical_qc_status": "failed",
                    "canonical_qc": qc_report,
                    "canonical_qc_history": qc_history,
                    "canonical_best_candidate": best_candidate,
                    "canonical_repair_feedback": repair_feedback,
                    "visual_lock": "QC_FAILED",
                },
            )
            _publish_canonical_media(failed_resource)
            emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_QC_FAILED", resource_type, entity_id,
                                severity="WARN", message="Canonical Vision QC chưa đạt.",
                                payload={"score": qc_report.get("overall_score"), "final_attempt": final_attempt})
            if final_attempt:
                if best_candidate and best_candidate.get("local_path"):
                    best_qc = best_candidate.get("qc") if isinstance(best_candidate.get("qc"), dict) else qc_report
                    update_resource_binding(
                        project_id,
                        resource_type,
                        entity_id,
                        provider="flow",
                        local_path=str(best_candidate.get("local_path") or ""),
                        status="error",
                        error=f"CANONICAL_QC_FAILED: best_score={best_qc.get('overall_score')}",
                        metadata_patch={
                            "canonical_filename": best_candidate.get("canonical_filename"),
                            "canonical_sha256": best_candidate.get("canonical_sha256"),
                            "canonical_selected_source_version": best_candidate.get("source_asset_version"),
                            "canonical_qc_status": "failed",
                            "canonical_qc": best_qc,
                            "canonical_qc_history": qc_history,
                            "canonical_best_candidate": best_candidate,
                            "canonical_repair_feedback": str(best_qc.get("repair_feedback") or repair_feedback),
                            "visual_lock": "QC_FAILED",
                        },
                    )
                raise RuntimeError(
                    "CANONICAL_QC_FAILED: Ảnh không vượt qua hard-gate sau "
                    f"{max(1, max_qc_attempts)} lần tạo. "
                    + (repair_feedback[:1200] if repair_feedback else "")
                )

        except CanonicalGenerationStopped:
            raise
        except Exception as exc:
            if str(exc).startswith("CANONICAL_QC_FAILED:"):
                raise
            failed_resource = update_resource_binding(
                project_id,
                resource_type,
                entity_id,
                provider="flow",
                status="error",
                error=str(exc)[:2000],
                metadata_patch={
                    "generation_status": "failed",
                    "generation_provider": provider,
                    "generation_model": model,
                    "generation_prompt": prompt,
                    "generation_attempt": attempt,
                    "canonical_qc_status": "error",
                    "visual_lock": "QC_ERROR",
                },
            )
            if failed_resource.get("local_path"):
                _publish_canonical_media(failed_resource)
            raise

    raise RuntimeError("CANONICAL_QC_FAILED: Không tạo được canonical asset đạt chuẩn.")
async def qc_existing_canonical_resource(
    project_id: str,
    resource_type: str,
    entity_id: str,
    *,
    auto_repair: bool = False,
    repair_provider: str = "flow",
    repair_model: str = DEFAULT_FLOW_IMAGE_MODEL,
) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    resource = get_project_resource(project_id, resource_type, entity_id, "flow")
    if not resource:
        raise ValueError("Không tìm thấy canonical resource.")
    local_path = str(resource.get("local_path") or "")
    if not local_path:
        raise ValueError("Canonical resource chưa có file ảnh.")

    entity = _entity(project, resource_type, entity_id)
    was_locked = resource.get("status") == "locked"
    update_resource_binding(
        project_id,
        resource_type,
        entity_id,
        provider="flow",
        status="pending",
        error=None,
        metadata_patch={
            "canonical_qc_status": "running",
            "visual_lock": "QC_RUNNING",
        },
    )
    try:
        report = await evaluate_canonical_asset(project, resource_type, entity_id, entity, local_path)
    except Exception as exc:
        update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider="flow",
            status="error",
            error=str(exc)[:2000],
            metadata_patch={
                "canonical_qc_status": "error",
                "visual_lock": "QC_ERROR",
            },
        )
        raise
    metadata = dict(resource.get("metadata") or {})
    history = list(metadata.get("canonical_qc_history") or [])
    history.append({
        "asset_version": metadata.get("asset_version"),
        "attempt": metadata.get("generation_attempt"),
        "passed": bool(report.get("passed")),
        "overall_score": report.get("overall_score"),
        "model": report.get("model"),
        "hard_gate": report.get("hard_gate"),
        "issues": report.get("issues") or [],
        "audit_existing": True,
    })
    history = history[-10:]

    if report.get("passed") is True:
        next_status = "locked" if was_locked else "ready"
        return _publish_canonical_media(update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider="flow",
            status=next_status,
            error=None,
            metadata_patch={
                "generation_status": "succeeded",
                "canonical_qc_status": "passed",
                "canonical_qc": report,
                "canonical_qc_history": history,
                "canonical_repair_feedback": "",
                "visual_lock": "LOCKED" if next_status == "locked" else "READY",
            },
        ))

    repair_feedback = str(report.get("repair_feedback") or "").strip()
    failed = update_resource_binding(
        project_id,
        resource_type,
        entity_id,
        provider="flow",
        status="error",
        error=f"CANONICAL_QC_FAILED: score={report.get('overall_score')}",
        metadata_patch={
            "canonical_qc_status": "failed",
            "canonical_qc": report,
            "canonical_qc_history": history,
            "canonical_repair_feedback": repair_feedback,
            "visual_lock": "QC_FAILED",
        },
    )
    if not auto_repair:
        return _publish_canonical_media(failed)

    repaired = await generate_canonical_resource(
        project_id,
        resource_type,
        entity_id,
        provider=repair_provider,
        model=repair_model,
        max_qc_attempts=MAX_QC_REGENERATIONS,
    )
    repaired_qc = (repaired.get("metadata") or {}).get("canonical_qc") or {}
    if was_locked and bool((repaired_qc.get("hard_gate") or {}).get("passed") is True):
        repaired = update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider="flow",
            status="locked",
            error=None,
            metadata_patch={"visual_lock": "LOCKED"},
        )
    return _publish_canonical_media(repaired)


async def qc_project_canonical_assets(
    project_id: str,
    *,
    resource_type: str | None = None,
    entity_ids: Iterable[str] | None = None,
    auto_repair: bool = False,
    repair_provider: str = "flow",
    repair_model: str = DEFAULT_FLOW_IMAGE_MODEL,
) -> dict:
    async with _project_lock(project_id):
        project = get_film_project(project_id)
        if not project:
            raise ValueError("Không tìm thấy dự án phim.")
        resources = sync_project_resources(project_id, "flow")
        wanted = {str(x) for x in (entity_ids or []) if str(x).strip()}
        selected = [
            item for item in resources
            if item.get("status") != "retired"
            and item.get("local_path")
            and (not resource_type or item.get("resource_type") == resource_type)
            and (not wanted or item.get("entity_id") in wanted)
        ]
        passed = []
        repaired = []
        failed = []
        for item in selected:
            try:
                before_version = int((item.get("metadata") or {}).get("asset_version") or 0)
                result = await qc_existing_canonical_resource(
                    project_id,
                    str(item["resource_type"]),
                    str(item["entity_id"]),
                    auto_repair=auto_repair,
                    repair_provider=repair_provider,
                    repair_model=repair_model,
                )
                after_version = int((result.get("metadata") or {}).get("asset_version") or 0)
                qc = (result.get("metadata") or {}).get("canonical_qc") or {}
                if bool((qc.get("hard_gate") or {}).get("passed") is True):
                    if after_version > before_version:
                        repaired.append(result)
                    else:
                        passed.append(result)
                else:
                    failed.append({
                        "resource_type": item.get("resource_type"),
                        "entity_id": item.get("entity_id"),
                        "error": result.get("error"),
                    })
            except Exception as exc:
                failed.append({
                    "resource_type": item.get("resource_type"),
                    "entity_id": item.get("entity_id"),
                    "error": str(exc)[:1500],
                })
        return {
            "project_id": project_id,
            "checked": len(selected),
            "passed": len(passed),
            "repaired": len(repaired),
            "failed": len(failed),
            "errors": failed,
            "resources": list_project_resources(project_id, "flow"),
        }

async def generate_project_canonical_assets(
    project_id: str,
    *,
    resource_type: str | None = None,
    entity_ids: Iterable[str] | None = None,
    provider: str = DEFAULT_IMAGE_PROVIDER,
    model: str = DEFAULT_IMAGE_MODEL,
    run_id: str | None = None,
) -> dict:
    async with _project_lock(project_id):
        project = get_film_project(project_id)
        if not project:
            raise ValueError("Không tìm thấy dự án phim.")
        resources = sync_project_resources(project_id, "flow")
        wanted = {str(x) for x in (entity_ids or []) if str(x).strip()}
        selected = [
            item for item in resources
            if item.get("status") not in {"retired", "locked"}
            and (bool(wanted) or item.get("status") in {"pending", "stale", "error"})
            and (not resource_type or item.get("resource_type") == resource_type)
            and (not wanted or item.get("entity_id") in wanted)
        ]
        if run_id:
            mark_run_started(project_id, run_id)
        results: list[dict] = []
        errors: list[dict] = []
        processed: set[tuple[str, str]] = set()
        stopped = False
        fatal_error: str | None = None
        fatal_error_code: str | None = None
        for item in selected:
            typ = str(item["resource_type"])
            entity_id = str(item["entity_id"])
            if run_id and stop_requested(project_id, run_id):
                stopped = True
                break
            processed.add((typ, entity_id))
            set_current_resource(project_id, run_id or "", typ, entity_id)
            mark_resource(project_id, typ, entity_id, "starting", run_id=run_id, status="pending", error=None)
            emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_STARTED", typ, entity_id,
                                message="Bắt đầu xử lý ảnh chuẩn.")
            try:
                result = await generate_canonical_resource(
                    project_id, typ, entity_id, provider=provider, model=model, run_id=run_id,
                )
                results.append(result)
                if run_id:
                    bump_result(project_id, run_id, completed=1)
                emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_COMPLETED", typ, entity_id,
                                    message="Ảnh chuẩn đã hoàn tất và được lưu.")
            except CanonicalGenerationStopped:
                stopped = True
                break
            except Exception as exc:
                message = str(exc)[:1500]
                error_code = render_error_code(message)
                errors.append({"resource_type": typ, "entity_id": entity_id, "error": message, "error_code": error_code})
                mark_resource(
                    project_id, typ, entity_id, "failed", run_id=run_id, status="error", error=message,
                    metadata_patch={"provider_error_code": error_code, "last_error_code": error_code},
                )
                if run_id:
                    bump_result(project_id, run_id, failed=1)
                emit_resource_event(
                    project_id, run_id, "CANONICAL_RESOURCE_FAILED", typ, entity_id,
                    severity="ERROR", message=message, payload={"error_code": error_code},
                )
                if provider == "flow" and is_flow_dependency_error(message):
                    fatal_error = message
                    fatal_error_code = error_code
                    break

        if stopped or fatal_error:
            for item in selected:
                typ = str(item["resource_type"])
                entity_id = str(item["entity_id"])
                if (typ, entity_id) in processed:
                    continue
                if stopped:
                    mark_resource(project_id, typ, entity_id, "stopped", run_id=run_id, status="pending", error=None)
                    emit_resource_event(project_id, run_id, "CANONICAL_RESOURCE_STOPPED", typ, entity_id,
                                        severity="WARN", message="Đã dừng theo yêu cầu người dùng.")
                else:
                    blocked = f"CANONICAL_BATCH_BLOCKED: {fatal_error}"[:2000]
                    mark_resource(
                        project_id, typ, entity_id, "blocked", run_id=run_id, status="error", error=blocked,
                        metadata_patch={
                            "provider_error_code": fatal_error_code or "CANONICAL_BATCH_BLOCKED",
                            "blocked_by_error_code": fatal_error_code,
                        },
                    )
                    emit_resource_event(
                        project_id, run_id, "CANONICAL_RESOURCE_FAILED", typ, entity_id,
                        severity="ERROR", message=blocked,
                        payload={"error_code": "CANONICAL_BATCH_BLOCKED", "blocked_by_error_code": fatal_error_code},
                    )

        if run_id:
            if stopped:
                finish_run(project_id, run_id, "stopped", completed=len(results), failed=len(errors))
            elif errors:
                status = "failed_partial" if results else "failed"
                finish_run(project_id, run_id, status, completed=len(results), failed=len(errors), error=fatal_error or errors[-1]["error"])
            else:
                finish_run(project_id, run_id, "completed", completed=len(results), failed=0)

        return {
            "project_id": project_id,
            "run_id": run_id,
            "requested": len(selected),
            "succeeded": len(results),
            "failed": len(errors),
            "stopped": stopped,
            "errors": errors,
            "resources": list_project_resources(project_id, "flow"),
        }
