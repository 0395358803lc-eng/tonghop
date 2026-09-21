from __future__ import annotations

import asyncio
import os
import re
import uuid

from .film_acceptance_snapshot import ensure_acceptance_snapshot
from .film_media_service import register_scene_video_from_job
from .film_media_store import get_media, get_selected_media, output_key_for
from .film_flow_errors import is_flow_dependency_error, is_terminal_render_error, render_error_code
from .film_production_gate import evaluate_production_gate_v2
from .film_qc_service import get_qc_status
from .film_render_adapters import get_active_adapter
from .film_render_store import (
    ACTIVE_RENDER_STATUSES,
    create_render_jobs,
    get_active_scene_job,
    get_render_job,
    refresh_render_job_context,
    retry_render_job,
    update_render_job,
)
from .film_repair_service import build_repair_prompt, is_better_candidate
from .film_resource_store import scene_resource_manifest
from .film_scene_qc_v2 import run_render_qc_v2
from .film_scene_state_store import (
    DEFAULT_MAX_RETRIES,
    acquire_execution_lease,
    append_run_log,
    create_run,
    ensure_scene_states,
    execution_lease_active,
    get_active_run,
    get_ledger,
    get_scene_state,
    heartbeat_execution_lease,
    latest_run,
    list_candidates,
    list_ledgers,
    list_scene_states,
    has_previous_scene,
    previous_approved_scene,
    record_candidate,
    release_execution_lease,
    save_ledger,
    select_best_candidate,
    update_run,
    upsert_scene_state,
)
from .film_store import get_film_project
from .flow_store import get_flow_settings

MAX_RETRIES = max(1, min(int(os.getenv("FILM_PIPELINE_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))), 3))
REFERENCE_PRIORITY = ("character", "previous_last_frame", "location", "critical_prop", "secondary_prop")
CANONICAL_RESOURCE_TYPES = frozenset({"character", "location", "prop"})
_PIPELINE_OWNERS: dict[str, str] = {}

FLOW_PROVIDER_FICTIONAL_CONTEXT = (
    "[FICTIONAL_CHARACTER_CONTEXT]\n"
    "All named characters in this scene are original fictional characters created for this story. "
    "They are not based on, intended to resemble, or impersonate any real person. "
    "Preserve only the locked fictional canonical design and story continuity."
)


def _flow_provider_safety_prompt(prompt: str | None) -> str:
    base = str(prompt or "").strip()
    if not base:
        return FLOW_PROVIDER_FICTIONAL_CONTEXT
    if "[FICTIONAL_CHARACTER_CONTEXT]" in base:
        return base
    return f"{FLOW_PROVIDER_FICTIONAL_CONTEXT}\n\n{base}"


def _mark_flow_dependency_blocked(
    project_id: str,
    scene_id: str,
    scene_index: int,
    attempt: int,
    job: dict,
    message: str,
) -> str:
    code = render_error_code(message)
    stored = get_render_job(str(job.get("id") or "")) or job
    status = str(stored.get("status") or "generating")
    if status not in ACTIVE_RENDER_STATUSES:
        status = "generating"
    update_render_job(
        stored["id"],
        status=status,
        progress=int(stored.get("progress") or 35),
        error=message,
        provider_error_code=code,
    )
    upsert_scene_state(
        project_id,
        scene_id,
        scene_index,
        status="BLOCKED",
        attempt=attempt,
        current_job_id=stored["id"],
        error=message,
        blocked_reason=message,
    )
    return code


def pipeline_worker_active(project_id: str) -> bool:
    if _PIPELINE_OWNERS.get(project_id):
        return True
    return execution_lease_active(project_id)


def pipeline_status(project_id: str) -> dict:
    run = get_active_run(project_id) or latest_run(project_id)
    scenes = list_scene_states(project_id)
    by_status: dict[str, int] = {}
    for scene in scenes:
        key = str(scene.get("status") or "LOCKED")
        by_status[key] = by_status.get(key, 0) + 1
    waiting = sum(by_status.get(key, 0) for key in ("LOCKED", "WAITING_REFERENCE", "QUEUED"))
    generating = sum(by_status.get(key, 0) for key in ("GENERATING", "QC_RUNNING", "REGENERATING"))
    snapshot = (run or {}).get("gate") if run else None
    return {
        "project_id": project_id,
        "run": run,
        "scenes": scenes,
        "ledgers": list_ledgers(project_id),
        "gate": snapshot,
        "run_gate": snapshot,
        "worker_active": pipeline_worker_active(project_id),
        "counts": {
            "total": len(scenes),
            "approved": by_status.get("APPROVED", 0),
            "generating": generating,
            "qc_failed": by_status.get("QC_FAILED", 0),
            "blocked": by_status.get("BLOCKED", 0),
            "waiting": waiting,
            "stale": by_status.get("STALE", 0),
            "by_status": by_status,
        },
    }


def _ordered_scenes(project: dict, from_scene_id: str | None = None, scene_limit: int | None = None) -> list[dict]:
    scenes = sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0))
    if from_scene_id:
        start = next((i for i, scene in enumerate(scenes) if scene.get("id") == from_scene_id), 0)
        scenes = scenes[start:]
    if scene_limit:
        scenes = scenes[: int(scene_limit)]
    return scenes


def _reference_flags(item: dict, kind: str) -> tuple[bool, bool]:
    required = item.get("required")
    critical = item.get("critical")
    optional = bool(item.get("optional"))
    if kind == "character" or kind == "previous_last_frame":
        return True, True
    if kind == "location":
        if required is None:
            required = True
        if critical is None:
            critical = bool(required)
        return bool(required) and not optional, bool(critical) and not optional
    if kind == "prop":
        if optional or required is False:
            return False, False
        if required is None:
            required = True
        if critical is None:
            critical = bool(item.get("critical_prop") or ((item.get("metadata") or {}).get("critical") if isinstance(item.get("metadata"), dict) else False))
        return bool(required), bool(critical)
    return bool(required) if required is not None else False, bool(critical) if critical is not None else False


def resolve_reference_priority(manifest: dict, previous_last_frame: str | None, max_references: int | None) -> dict:
    ranked = []
    for item in manifest.get("references") or []:
        kind = str(item.get("resource_type") or "")
        required, critical = _reference_flags(item, kind)
        if kind == "character":
            priority, bucket = 1, "character"
        elif kind == "location":
            priority, bucket = 3, "location"
        elif kind == "prop":
            bucket = "critical_prop" if critical else "secondary_prop"
            priority = 4 if bucket == "critical_prop" else 5
        else:
            priority, bucket = 9, kind or "other"
        ranked.append({"priority": priority, "bucket": bucket, "item": item, "required": required, "critical": critical})
    if previous_last_frame:
        ranked.append({
            "priority": 2,
            "bucket": "previous_last_frame",
            "item": {"resource_type": "previous_last_frame", "local_path": previous_last_frame, "required": True, "critical": True},
            "required": True,
            "critical": True,
        })
    ranked.sort(key=lambda x: x["priority"])
    dropped = []
    overflow = None
    selected = ranked
    if max_references is not None and len(ranked) > int(max_references):
        selected = ranked[: int(max_references)]
        dropped = ranked[int(max_references):]
        dropped_critical = [x for x in dropped if x.get("required") and x.get("critical")]
        overflow = {
            "code": "REFERENCE_CAPACITY_EXCEEDED",
            "detail": f"Required {len(ranked)} references exceed model max_references={max_references}.",
            "required": [x["bucket"] for x in ranked if x.get("required")],
            "priority": list(REFERENCE_PRIORITY),
            "blocked": bool(dropped_critical),
            "dropped_critical": [x.get("bucket") for x in dropped_critical],
        }
    return {
        "selected": selected,
        "dropped": dropped,
        "overflow": overflow,
        "count": len(ranked),
        "max_references": max_references,
    }


def dropped_required_critical(resolved: dict) -> list[dict]:
    return [x for x in (resolved.get("dropped") or []) if x.get("required") and x.get("critical")]


def enforce_reference_capacity(resolved: dict) -> None:
    dropped = dropped_required_critical(resolved)
    if not dropped:
        return
    buckets = [x.get("bucket") for x in dropped]
    detail = ((resolved.get("overflow") or {}).get("detail") or f"Required critical references dropped: {buckets}")
    raise RuntimeError(f"REFERENCE_CAPACITY_EXCEEDED: {detail}")


def _is_boundary_item(item: dict | None, bucket: str | None = None) -> bool:
    kind = str((item or {}).get("resource_type") or bucket or "")
    return kind == "previous_last_frame"


def sanitize_canonical_resource_manifest(manifest: dict | None) -> dict:
    filtered = dict(manifest or {})
    filtered["references"] = [
        item for item in (filtered.get("references") or [])
        if isinstance(item, dict) and str(item.get("resource_type") or "") in CANONICAL_RESOURCE_TYPES
    ]
    return filtered


def build_flow_reference_payload(manifest: dict, previous_last_frame: str | None, max_references: int | None) -> dict:
    resolved = resolve_reference_priority(manifest, previous_last_frame, max_references)
    selected_entries = list(resolved.get("selected") or [])
    if max_references is not None:
        selected_entries = selected_entries[: int(max_references)]
    selected_items = []
    canonical_items = []
    boundary = None
    for entry in selected_entries:
        item = dict(entry.get("item") or {})
        item["bucket"] = entry.get("bucket")
        item["priority"] = entry.get("priority")
        item["required"] = entry.get("required")
        item["critical"] = entry.get("critical")
        selected_items.append(item)
        if _is_boundary_item(item, entry.get("bucket")):
            boundary = {
                "resource_type": "previous_last_frame",
                "url": item.get("local_path") or previous_last_frame,
                "bucket": "previous_last_frame",
                "priority": entry.get("priority"),
                "required": True,
                "critical": True,
            }
            continue
        if str(item.get("resource_type") or "") in CANONICAL_RESOURCE_TYPES:
            canonical_items.append(item)
    selection = {
        "buckets": [x.get("bucket") for x in selected_entries],
        "count": len(selected_items),
        "max_references": max_references,
        "dropped": [x.get("bucket") for x in (resolved.get("dropped") or [])],
        "canonical_count": len(canonical_items),
        "boundary": bool(boundary),
    }
    filtered = sanitize_canonical_resource_manifest(dict(manifest or {}))
    filtered["references"] = canonical_items
    filtered["selection"] = selection
    return {
        "resolved": resolved,
        "resource_manifest": filtered,
        "canonical_manifest": filtered,
        "boundary_reference": boundary,
        "reference_image_url": (boundary or {}).get("url"),
        "reference_selection": selection,
        "selected": selected_items,
    }


def _previous_last_frame(project_id: str, scene_index: int) -> dict:
    if not has_previous_scene(project_id, scene_index):
        return {"required": False, "url": None, "scene_id": None, "approved": True}
    prev_state = previous_approved_scene(project_id, scene_index)
    if not prev_state:
        return {"required": True, "url": None, "scene_id": None, "approved": False}
    ledger = get_ledger(project_id, prev_state["scene_id"]) or {}
    selected = None
    if prev_state.get("selected_media_id"):
        selected = get_media(prev_state["selected_media_id"])
    if not selected:
        selected = get_selected_media(project_id, output_key_for(role="scene_video", scene_id=prev_state["scene_id"]))
    meta = (selected or {}).get("metadata") or {}
    url = ledger.get("accepted_last_frame") or meta.get("last_frame_url")
    return {
        "required": True,
        "url": url,
        "scene_id": prev_state["scene_id"],
        "approved": True,
        "selected_media_id": (selected or {}).get("id") or ledger.get("selected_media_id"),
        "media": selected,
        "ledger": ledger,
    }


def _ledger_from_scene(scene: dict, job: dict, media: dict | None, qc_v2: dict) -> dict:
    end_state = scene.get("end_state_structured") if isinstance(scene.get("end_state_structured"), dict) else {}
    report = qc_v2.get("qc") if isinstance(qc_v2.get("qc"), dict) else {}
    return {
        "scene_id": scene.get("id"),
        "selected_media_id": (media or {}).get("id"),
        "accepted_first_frame": job.get("first_frame_url"),
        "accepted_last_frame": job.get("last_frame_url"),
        "character_positions": end_state.get("positions") or {},
        "character_pose": end_state.get("pose") or scene.get("end_state"),
        "character_wardrobe": end_state.get("wardrobe"),
        "character_visibility": {str(cid): True for cid in (scene.get("characters") or [])},
        "prop_owner": end_state.get("prop_owner") or {},
        "prop_holder": end_state.get("prop_holder") or {},
        "prop_location": end_state.get("prop_location") or {},
        "prop_state": end_state.get("prop_state") or {},
        "location_id": scene.get("location_id"),
        "time_of_day": scene.get("atmosphere"),
        "lighting_state": scene.get("lighting"),
        "camera_direction": scene.get("camera"),
        "dialogue_state": "delivered" if (scene.get("dialogue") or scene.get("voiceover")) else "none",
        "audio_state": ((report.get("evidence") or {}).get("audio") if isinstance(report.get("evidence"), dict) else None),
        "snapshot": {
            "prompt": job.get("prompt"),
            "provider": job.get("adapter"),
            "qc_status": qc_v2.get("qc_status"),
            "consistency_score": qc_v2.get("consistency_score"),
            "media_id": (media or {}).get("id"),
            "media_version": (media or {}).get("version"),
        },
    }


async def _max_video_references() -> int | None:
    if not get_flow_settings():
        return None
    try:
        import httpx
        from .flow_bridge_client import _credentials, _headers
        base_url, api_key = _credentials()
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{base_url}/v1/capabilities/video", headers=_headers(api_key))
            if response.status_code >= 400:
                return None
            data = response.json()
        return data.get("max_references") or (data.get("ingredients") or {}).get("max")
    except Exception:
        return None


async def _execute_job(job: dict, project: dict, scene: dict, reference_payload: dict | None = None) -> dict:
    adapter = get_active_adapter()
    if not adapter.configured:
        raise RuntimeError("FLOW: video capability unavailable")
    current = refresh_render_job_context(job["id"]) or get_render_job(job["id"])
    provider_prompt = _flow_provider_safety_prompt((current or {}).get("prompt"))
    if provider_prompt != str((current or {}).get("prompt") or "").strip():
        current = update_render_job(job["id"], prompt=provider_prompt) or current
    reference = current.get("reference") or {}
    resource_manifest = scene_resource_manifest(project, scene, "flow")
    if not resource_manifest.get("ready"):
        missing = ", ".join(resource_manifest.get("missing") or [])
        raise RuntimeError(f"RESOURCE_LOCK_INCOMPLETE: {missing}")
    if reference.get("previous_scene_id") and not reference.get("previous_last_frame_url"):
        raise RuntimeError("CONTINUITY_REFERENCE_PENDING: previous accepted last frame missing")
    built = reference_payload or build_flow_reference_payload(
        resource_manifest, reference.get("previous_last_frame_url"), await _max_video_references()
    )
    mixed = any(
        str(item.get("resource_type") or "") == "previous_last_frame"
        for item in ((built.get("resource_manifest") or {}).get("references") or [])
        if isinstance(item, dict)
    )
    if mixed or built.get("canonical_manifest") is None:
        built = build_flow_reference_payload(
            resource_manifest, reference.get("previous_last_frame_url"), await _max_video_references()
        )
    selected_manifest = sanitize_canonical_resource_manifest(
        built.get("canonical_manifest") or built.get("resource_manifest") or {}
    )
    try:
        enforce_reference_capacity(built.get("resolved") or {})
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    boundary = built.get("boundary_reference")
    update_render_job(job["id"], status="preparing", progress=10, error=None)
    payload = {
        "project_id": project["id"],
        "idempotency_key": job["id"],
        "flow_project_id": project.get("settings", {}).get("flow_project_id"),
        "model": project.get("settings", {}).get("flow_model"),
        "scene_id": scene["id"],
        "prompt": current["prompt"],
        "duration": scene["duration"],
        "aspect_ratio": project.get("settings", {}).get("aspect_ratio", "16:9"),
        "resolution": project.get("settings", {}).get("resolution", "1080p"),
        "reference_image_url": (boundary or {}).get("url"),
        "previous_video_url": reference.get("previous_result_url"),
        "previous_end_state": reference.get("previous_end_state"),
        "start_state": scene.get("start_state"),
        "end_state": scene.get("end_state"),
        "resource_manifest": selected_manifest,
        "reference_selection": built.get("reference_selection") or selected_manifest.get("selection"),
        "boundary_reference": boundary,
    }
    update_render_job(job["id"], status="generating", progress=35)
    result = await adapter.render(payload)
    update_render_job(job["id"], status="generating", progress=90, provider_job_id=result.get("provider_job_id"))
    return update_render_job(
        job["id"], status="completed", progress=100, result_url=result["result_url"],
        first_frame_url=result.get("first_frame_url"), last_frame_url=result.get("last_frame_url"),
        qc_status="pending", qc_json={"message": "Video đã render; đang chuyển sang Quality Control V2."},
        error=None, provider_error_code=None,
    )


def _merged_scene_snapshot(project_id: str, scene_id: str, **updates) -> dict:
    snapshot = dict((get_scene_state(project_id, scene_id) or {}).get("snapshot") or {})
    snapshot.update(updates)
    return snapshot


def _prepare_scene_status(project_id: str, scene: dict, run_id: str, from_scene_id: str | None) -> None:
    scene_id = scene["id"]
    scene_index = int(scene.get("scene_index") or 0)
    existing = get_scene_state(project_id, scene_id) or {}
    if existing.get("status") == "APPROVED":
        upsert_scene_state(project_id, scene_id, scene_index, current_run_id=run_id)
        return
    if existing.get("status") == "REGENERATING":
        upsert_scene_state(project_id, scene_id, scene_index, current_run_id=run_id, error=None, blocked_reason=None)
        return
    existing_media = get_selected_media(project_id, output_key_for(role="scene_video", scene_id=scene_id))
    force_generation = bool((existing.get("snapshot") or {}).get("operator_force_generation"))
    if (
        existing.get("status") != "STALE"
        and not force_generation
        and existing_media
        and existing_media.get("status") == "completed"
        and existing_media.get("qc_status") == "passed"
    ):
        meta = existing_media.get("metadata") or {}
        job = get_render_job(str(existing_media.get("provider_job_id") or "")) or {
            "first_frame_url": meta.get("first_frame_url"),
            "last_frame_url": meta.get("last_frame_url"),
            "prompt": meta.get("prompt"),
            "adapter": existing_media.get("provider"),
        }
        qc = {"qc_status": "passed", "consistency_score": existing_media.get("qc_score"), "qc": existing_media.get("qc") or {}}
        ledger = save_ledger(project_id, scene_id, _ledger_from_scene(scene, job, existing_media, qc))
        upsert_scene_state(
            project_id, scene_id, scene_index, status="APPROVED", current_run_id=run_id,
            selected_media_id=existing_media.get("id"), error=None, blocked_reason=None,
            snapshot={"ledger": ledger, "media_id": existing_media.get("id"), "recovered": True}, force=True,
        )
        ensure_acceptance_snapshot(project_id, scene_id)
        return
    if has_previous_scene(project_id, scene_index) and not previous_approved_scene(project_id, scene_index):
        if from_scene_id and scene_id == from_scene_id:
            raise ValueError(f"{scene_id}: previous scene not approved")
        upsert_scene_state(
            project_id, scene_id, scene_index, status="WAITING_REFERENCE", current_run_id=run_id,
            max_retries=MAX_RETRIES, blocked_reason=f"{scene_id}: previous scene not approved",
        )
        return
    existing_attempt = int(existing.get("attempt") or 0) if existing.get("attempt") is not None else 0
    next_attempt = existing_attempt + 1 if existing.get("status") == "STALE" else existing_attempt
    upsert_scene_state(
        project_id, scene_id, scene_index, status="QUEUED", current_run_id=run_id,
        max_retries=MAX_RETRIES,
        attempt=next_attempt,
        error=None, blocked_reason=None,
    )


def start_pipeline(project_id: str, *, from_scene_id: str | None = None, scene_limit: int | None = None) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    active = get_active_run(project_id)
    if active and active.get("status") in {"running", "paused", "stopping"}:
        return pipeline_status(project_id)
    gate = evaluate_production_gate_v2(project_id, persist=False)
    if not gate.get("final_gate"):
        raise ValueError("PRODUCTION_GATE_V2_BLOCKED: " + "; ".join(
            f"{item.get('scene')}: {item.get('detail')}" for item in (gate.get("errors") or [])[:8]
        ))
    if from_scene_id:
        target = next((scene for scene in (project.get("scenes") or []) if scene.get("id") == from_scene_id), None)
        if not target:
            raise ValueError("Không tìm thấy scene để tiếp tục.")
        idx = int(target.get("scene_index") or 0)
        if has_previous_scene(project_id, idx):
            prev = _previous_last_frame(project_id, idx)
            if not prev.get("approved"):
                raise ValueError(f"{from_scene_id}: previous scene not approved")
            if not prev.get("url"):
                raise ValueError(f"{from_scene_id}: previous accepted last frame missing")
    scenes = _ordered_scenes(project, from_scene_id, scene_limit)
    if not scenes:
        raise ValueError("Không có scene hợp lệ để chạy pipeline.")
    ensure_scene_states(project_id, project.get("scenes") or [], MAX_RETRIES)
    run = create_run(project_id, from_scene_id=from_scene_id, scene_limit=scene_limit, gate=gate)
    for scene in scenes:
        _prepare_scene_status(project_id, scene, run["id"], from_scene_id)
    append_run_log(run["id"], {"action": "start", "from_scene_id": from_scene_id, "scene_limit": scene_limit, "scene_count": len(scenes)})
    return pipeline_status(project_id)


def pause_pipeline(project_id: str) -> dict:
    run = get_active_run(project_id)
    if not run:
        raise ValueError("Không có pipeline đang chạy.")
    if run.get("status") == "running":
        update_run(run["id"], status="paused")
        append_run_log(run["id"], {"action": "pause"})
    return pipeline_status(project_id)


def resume_pipeline(project_id: str) -> dict:
    run = get_active_run(project_id)
    if not run:
        raise ValueError("Không có pipeline để resume.")
    if run.get("status") == "paused":
        update_run(run["id"], status="running", stop_after_current=False)
        append_run_log(run["id"], {"action": "resume"})
    return pipeline_status(project_id)


def stop_pipeline(project_id: str) -> dict:
    run = get_active_run(project_id)
    if not run:
        raise ValueError("Không có pipeline để dừng.")
    if run.get("status") == "running":
        update_run(run["id"], status="stopping", stop_after_current=True)
        append_run_log(run["id"], {"action": "stop_after_current"})
    elif run.get("status") in {"paused", "stopping"}:
        update_run(run["id"], status="stopped")
        append_run_log(run["id"], {"action": "stop"})
    return pipeline_status(project_id)


DEPENDENCY_RETRY_BLOCKERS = (
    "RESOURCE_LOCK_INCOMPLETE",
    "canonical media",
    "FLOW:",
    "SESSION_EXPIRED",
    "REAUTH_REQUIRED",
    "BRIDGE_AUTH_ERROR",
    "PROJECT_NOT_FOUND",
    "FLOW_PROJECT_NOT_FOUND",
    "FLOW_UI_CHANGED",
    "CAPABILITY_MISMATCH",
    "FLOW_CREDITS_INSUFFICIENT",
    "not authenticated",
    "previous scene not approved",
    "CONTINUITY_REFERENCE_PENDING",
    "previous accepted last frame",
    "video capability unavailable",
)


def retry_scene(project_id: str, scene_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scene = next((item for item in (project.get("scenes") or []) if item.get("id") == scene_id), None)
    if not scene:
        raise ValueError("Không tìm thấy scene.")
    state = get_scene_state(project_id, scene_id) or {}
    status = state.get("status")
    if status in {"REGENERATING", "QUEUED", "GENERATING", "QC_RUNNING"}:
        return pipeline_status(project_id)
    if status not in {"QC_FAILED", "BLOCKED"}:
        raise ValueError("Chỉ retry scene QC thất bại hoặc bị chặn vì QC.")
    blob = f"{state.get('blocked_reason') or ''} {state.get('error') or ''}"
    qc_retry = "QC V2 failed" in blob or "QC_FAILED" in blob or "hard=" in blob or status == "QC_FAILED"
    dependency = any(marker.lower() in blob.lower() for marker in DEPENDENCY_RETRY_BLOCKERS)
    if dependency and not qc_retry:
        raise ValueError("Không retry lỗi dependency. Sửa nguyên nhân trước.")
    scene_index = int(scene.get("scene_index") or state.get("scene_index") or 0)
    snapshot = dict(state.get("snapshot") or {})
    if qc_retry:
        previous_job = get_render_job(str(state.get("current_job_id") or "")) or {}
        base_prompt = previous_job.get("prompt") or scene.get("flow_prompt") or ""
        repair = build_repair_prompt(scene, {"qc": state.get("qc") or {}}, base_prompt)
        snapshot["operator_repair_prompt"] = repair["prompt"]
        snapshot["operator_repair_source_attempt"] = int(state.get("attempt") or 0)
        snapshot["operator_repair_failed_dimensions"] = repair["failed_dimensions"]
    if status == "QC_FAILED":
        upsert_scene_state(
            project_id, scene_id, scene_index,
            status="REGENERATING", error=None, blocked_reason=None,
            snapshot=snapshot,
        )
    else:
        # Operator-controlled recovery after a terminal BLOCKED state:
        # preserve all historical jobs/media/candidates, but allocate one new
        # monotonically increasing attempt and detach the old terminal job.
        # The force-generation marker prevents _prepare_scene_status() from
        # silently re-approving the previously selected media.
        next_attempt = int(state.get("attempt") or 0) + 1
        snapshot["operator_force_generation"] = True
        snapshot["operator_retry_source_attempt"] = int(state.get("attempt") or 0)
        upsert_scene_state(
            project_id,
            scene_id,
            scene_index,
            status="QUEUED",
            attempt=next_attempt,
            current_job_id=None,
            error=None,
            blocked_reason=None,
            snapshot=snapshot,
        )
    active = get_active_run(project_id)
    if active and active.get("status") in {"running", "paused", "stopping"}:
        if active.get("status") == "paused":
            update_run(active["id"], status="running", stop_after_current=False)
        append_run_log(active["id"], {"action": "retry_scene", "scene_id": scene_id})
        return pipeline_status(project_id)
    return start_pipeline(project_id, from_scene_id=scene_id, scene_limit=1)


def _next_scene(project: dict, run: dict) -> dict | None:
    scenes = _ordered_scenes(project, run.get("from_scene_id"), run.get("scene_limit"))
    for scene in scenes:
        state = get_scene_state(project["id"], scene["id"]) or {}
        if state.get("status") == "APPROVED":
            continue
        return scene
    return None


def _enter_generate_status(project_id: str, scene_id: str, scene_index: int, attempt: int) -> None:
    current = (get_scene_state(project_id, scene_id) or {}).get("status") or "LOCKED"
    if attempt > 0 and current == "QC_FAILED":
        upsert_scene_state(project_id, scene_id, scene_index, status="REGENERATING", attempt=attempt)
        current = "REGENERATING"
    if current in {"LOCKED", "WAITING_REFERENCE", "BLOCKED", "STALE"}:
        upsert_scene_state(project_id, scene_id, scene_index, status="QUEUED", attempt=attempt)
        current = "QUEUED"
    if current in {"QUEUED", "REGENERATING"}:
        upsert_scene_state(project_id, scene_id, scene_index, status="GENERATING", attempt=attempt)


async def process_one_scene(project_id: str, scene: dict, run: dict) -> dict:
    project = get_film_project(project_id)
    scene_id = scene["id"]
    scene_index = int(scene.get("scene_index") or 0)
    upsert_scene_state(project_id, scene_id, scene_index, current_run_id=run["id"])
    if has_previous_scene(project_id, scene_index):
        prev = _previous_last_frame(project_id, scene_index)
        if not prev.get("approved"):
            upsert_scene_state(project_id, scene_id, scene_index, status="BLOCKED", blocked_reason=f"{scene_id}: previous scene not approved", error=f"{scene_id}: previous scene not approved")
            raise RuntimeError(f"{scene_id}: previous scene not approved")
        if not prev.get("url"):
            upsert_scene_state(project_id, scene_id, scene_index, status="WAITING_REFERENCE", blocked_reason="previous accepted last frame missing")
            raise RuntimeError("CONTINUITY_REFERENCE_PENDING: previous accepted last frame missing")
    manifest = scene_resource_manifest(project, scene, "flow")
    if not manifest.get("ready"):
        missing = ", ".join(manifest.get("missing") or ["canonical reference"])
        upsert_scene_state(project_id, scene_id, scene_index, status="BLOCKED", blocked_reason=missing, error=missing)
        raise RuntimeError(f"RESOURCE_LOCK_INCOMPLETE: {missing}")
    previous = _previous_last_frame(project_id, scene_index)
    max_refs = await _max_video_references()
    cap_rows = []
    try:
        from .film_capability_matrix import canonical_model_name, evaluate_capability, list_capability_matrix
        cap_rows = list_capability_matrix(media_type="video")
        if cap_rows:
            settings = project.get("settings") or {}
            wanted = canonical_model_name(settings.get("flow_model") or "") or (settings.get("flow_model") or "")
            match = next((row for row in cap_rows if row.get("model") == wanted), None) or cap_rows[0]
            cap_max = match.get("max_references")
            if cap_max:
                cap_max = int(cap_max)
                max_refs = cap_max if max_refs is None else min(int(max_refs), cap_max)
    except Exception:
        cap_rows = []
    built_refs = build_flow_reference_payload(manifest, previous.get("url"), max_refs)
    resolved = built_refs["resolved"]
    try:
        enforce_reference_capacity(resolved)
    except RuntimeError as exc:
        detail = str(exc)
        upsert_scene_state(
            project_id,
            scene_id,
            scene_index,
            status="BLOCKED",
            blocked_reason=detail,
            error=detail,
            snapshot=_merged_scene_snapshot(
                project_id,
                scene_id,
                reference_overflow=resolved.get("overflow"),
                reference_selection=(built_refs["resource_manifest"] or {}).get("selection"),
            ),
        )
        raise
    if resolved.get("overflow"):
        upsert_scene_state(
            project_id,
            scene_id,
            scene_index,
            snapshot=_merged_scene_snapshot(
                project_id,
                scene_id,
                reference_overflow=resolved["overflow"],
                reference_selection=(built_refs["resource_manifest"] or {}).get("selection"),
            ),
        )
    try:
        if cap_rows:
            settings = project.get("settings") or {}
            cap = evaluate_capability(
                model=settings.get("flow_model"),
                media_type="video",
                duration=scene.get("duration"),
                resolution=settings.get("resolution"),
                reference_count=(built_refs.get("reference_selection") or {}).get("count"),
                aspect_ratio=settings.get("aspect_ratio"),
            )
            if cap.get("blocked"):
                detail = cap.get("detail") or "; ".join(x.get("detail") or x.get("code") for x in (cap.get("errors") or []))
                code = cap.get("code") or "CAPABILITY_BLOCKED"
                upsert_scene_state(project_id, scene_id, scene_index, status="BLOCKED", blocked_reason=f"{code}: {detail}", error=f"{code}: {detail}")
                raise RuntimeError(f"{code}: {detail}")
    except RuntimeError:
        raise
    except Exception:
        pass
    state = get_scene_state(project_id, scene_id) or {}
    attempt = int(state.get("attempt") or 0)
    max_retries = int(state.get("max_retries") or MAX_RETRIES)
    job = None
    current_id = state.get("current_job_id")
    if current_id:
        existing = get_render_job(str(current_id))
        if existing and existing.get("status") in ACTIVE_RENDER_STATUSES:
            job = existing
        elif existing and existing.get("status") == "completed" and existing.get("result_url") and existing.get("qc_status") not in {"passed"}:
            job = existing
    if job is None:
        job = get_active_scene_job(project_id, scene_id, attempt)
    last_qc = None
    while True:
        _enter_generate_status(project_id, scene_id, scene_index, attempt)
        if job is None:
            created = create_render_jobs(project_id, [scene_id], get_active_adapter().id, attempt=attempt)
            if len(created) != 1:
                raise RuntimeError("PIPELINE_QUEUE_CONTRACT: pipeline chỉ được queue đúng 1 scene.")
            job = created[0]
            current_state = get_scene_state(project_id, scene_id) or {}
            snapshot = dict(current_state.get("snapshot") or {})
            operator_repair_prompt = snapshot.get("operator_repair_prompt")
            junction_repair_prompt = snapshot.get("junction_repair_prompt")
            if operator_repair_prompt:
                # Full prompt override generated from the previously failed QC.
                update_render_job(job["id"], prompt=str(operator_repair_prompt).strip())
                snapshot.pop("operator_repair_prompt", None)
                snapshot.pop("operator_repair_source_attempt", None)
                snapshot.pop("operator_repair_failed_dimensions", None)
                upsert_scene_state(project_id, scene_id, scene_index, snapshot=snapshot)
                job = get_render_job(job["id"]) or job
            elif junction_repair_prompt:
                base = job.get("prompt") or scene.get("flow_prompt") or ""
                update_render_job(job["id"], prompt=f"{base}\n\n{junction_repair_prompt}".strip())
                job = get_render_job(job["id"]) or job
        upsert_scene_state(project_id, scene_id, scene_index, current_job_id=job["id"], attempt=attempt)
        update_run(run["id"], current_scene_id=scene_id, current_scene_index=scene_index)
        append_run_log(run["id"], {"action": "generate", "scene_id": scene_id, "job_id": job["id"], "attempt": attempt})
        skip_render = bool(job.get("status") == "completed" and job.get("result_url") and job.get("qc_status") not in {"passed"})
        try:
            completed = job if skip_render else await _execute_job(job, project, scene, built_refs)
        except Exception as exc:
            message = str(exc)[:2000]
            if is_terminal_render_error(message):
                code = render_error_code(message)
                update_render_job(
                    job["id"],
                    status="failed",
                    progress=100,
                    error=message,
                    provider_error_code=code,
                )
                upsert_scene_state(
                    project_id,
                    scene_id,
                    scene_index,
                    status="BLOCKED",
                    attempt=attempt,
                    current_job_id=job["id"],
                    error=message,
                    blocked_reason=message,
                )
                append_run_log(
                    run["id"],
                    {
                        "action": "terminal_render_blocked",
                        "scene_id": scene_id,
                        "job_id": job.get("id"),
                        "attempt": attempt,
                        "provider_error_code": code,
                    },
                )
                raise RuntimeError(message) from exc
            if is_flow_dependency_error(message):
                code = _mark_flow_dependency_blocked(
                    project_id,
                    scene_id,
                    scene_index,
                    attempt,
                    job,
                    message,
                )
                append_run_log(
                    run["id"],
                    {
                        "action": "dependency_blocked",
                        "scene_id": scene_id,
                        "job_id": job.get("id"),
                        "attempt": attempt,
                        "provider_error_code": code,
                    },
                )
                raise RuntimeError(message) from exc
            code = render_error_code(message)
            update_render_job(
                job["id"],
                status="failed",
                progress=100,
                error=message,
                provider_error_code=code,
            )
            upsert_scene_state(project_id, scene_id, scene_index, status="QC_FAILED", error=message)
            if attempt >= max_retries:
                upsert_scene_state(project_id, scene_id, scene_index, status="BLOCKED", blocked_reason=message)
                raise
            repair = build_repair_prompt(scene, last_qc or {}, job.get("prompt") or scene.get("flow_prompt") or "")
            nxt = retry_render_job(job["id"])
            update_render_job(nxt["id"], prompt=repair["prompt"])
            # Reload the retry row after updating its prompt. Otherwise the
            # in-memory row still carries the original base prompt.
            job = get_render_job(nxt["id"]) or nxt
            attempt += 1
            continue
        upsert_scene_state(project_id, scene_id, scene_index, status="QC_RUNNING")
        qc = await run_render_qc_v2(completed, project, scene)
        last_qc = qc
        update_render_job(job["id"], qc_status=qc["qc_status"], consistency_score=qc.get("consistency_score"), qc_json=qc.get("qc") or {})
        media = None
        try:
            media = register_scene_video_from_job(get_render_job(job["id"]) or completed, qc)
        except Exception:
            media = None
        report = qc.get("qc") if isinstance(qc.get("qc"), dict) else {}
        candidate = {
            "hard_gates_passed": bool((report.get("hard_gate") or {}).get("passed")) if report else qc.get("qc_status") == "passed",
            "dimensions_passed": int(report.get("dimensions_passed") or 0),
            "overall_score": qc.get("consistency_score"),
        }
        stored = record_candidate(
            project_id, scene_id, run_id=run["id"], job_id=job["id"], media_id=(media or {}).get("id"),
            attempt=attempt, hard_gates_passed=bool(candidate["hard_gates_passed"]),
            dimensions_passed=candidate["dimensions_passed"], overall_score=candidate["overall_score"], qc=report,
        )
        best = select_best_candidate(project_id, scene_id) or stored
        upsert_scene_state(
            project_id, scene_id, scene_index, best_media_id=best.get("media_id"), best_score=best.get("overall_score"),
            best_rank={"hard_gates_passed": bool(best.get("hard_gates_passed")), "dimensions_passed": best.get("dimensions_passed"), "overall_score": best.get("overall_score")},
            qc=report,
        )
        if qc.get("qc_status") == "passed" and candidate["hard_gates_passed"]:
            selected_id = (media or {}).get("id") if (media or {}).get("is_selected") else best.get("media_id")
            ledger = save_ledger(project_id, scene_id, _ledger_from_scene(scene, get_render_job(job["id"]) or completed, media, qc))
            upsert_scene_state(
                project_id, scene_id, scene_index, status="APPROVED",
                selected_media_id=selected_id or ledger.get("selected_media_id"), error=None, blocked_reason=None,
                qc=report, snapshot={"ledger": ledger, "job_id": job["id"], "media_id": selected_id},
            )
            append_run_log(run["id"], {"action": "approved", "scene_id": scene_id, "media_id": selected_id, "attempt": attempt})
            ensure_acceptance_snapshot(project_id, scene_id)
            try:
                from .film_boundary_service import recheck_junctions_for_scene_async
                await recheck_junctions_for_scene_async(project_id, scene_id)
            except Exception as exc:
                from .film_event_store import emit_event
                emit_event(
                    project_id,
                    "JUNCTION_QC_FAILED",
                    scene_id=scene_id,
                    severity="ERROR",
                    payload={"stage": "post_scene_approval", "error": str(exc)[:800]},
                )
            return get_scene_state(project_id, scene_id)
        message = f"QC V2 failed score={qc.get('consistency_score')}"
        if (report.get("hard_gate") or {}).get("failed"):
            message += " hard=" + ",".join(report["hard_gate"]["failed"])
        update_render_job(job["id"], status="failed", progress=100, error=message, provider_error_code="QC_FAILED")
        repair = build_repair_prompt(scene, qc, job.get("prompt") or scene.get("flow_prompt") or "")
        repairs = list((get_scene_state(project_id, scene_id) or {}).get("repair") or [])
        repairs.append({"attempt": attempt, "failed": repair["failed_dimensions"], "lines": repair["repair_lines"]})
        upsert_scene_state(project_id, scene_id, scene_index, status="QC_FAILED", error=message, qc=report, repair=repairs)
        append_run_log(run["id"], {"action": "qc_failed", "scene_id": scene_id, "attempt": attempt, "repair": repair["failed_dimensions"]})
        if attempt >= max_retries:
            upsert_scene_state(project_id, scene_id, scene_index, status="BLOCKED", blocked_reason=message)
            raise RuntimeError(message)
        nxt = retry_render_job(job["id"])
        update_render_job(nxt["id"], prompt=repair["prompt"])
        # Reload after the DB prompt update so the provider receives the
        # repaired prompt on the next attempt instead of the stale base prompt.
        job = get_render_job(nxt["id"]) or nxt
        attempt += 1


async def process_pipeline(project_id: str) -> dict:
    if project_id in _PIPELINE_OWNERS:
        return pipeline_status(project_id)
    worker_id = str(uuid.uuid4())
    _PIPELINE_OWNERS[project_id] = worker_id
    try:
        from .film_recovery_service import reconcile_project
        reconcile_project(project_id)
    except Exception:
        pass
    run = get_active_run(project_id)
    lease = acquire_execution_lease(project_id, worker_id=worker_id, run_id=(run or {}).get("id"))
    if lease.get("worker_id") != worker_id:
        _PIPELINE_OWNERS.pop(project_id, None)
        return pipeline_status(project_id)
    try:
        while True:
            run = get_active_run(project_id)
            if not run:
                return pipeline_status(project_id)
            heartbeat_execution_lease(project_id, worker_id, run_id=run.get("id"), scene_id=run.get("current_scene_id"))
            if run.get("status") == "paused":
                return pipeline_status(project_id)
            if run.get("status") == "stopping" and not run.get("current_scene_id"):
                update_run(run["id"], status="stopped")
                return pipeline_status(project_id)
            if run.get("status") not in {"running", "stopping"}:
                return pipeline_status(project_id)
            project = get_film_project(project_id)
            if not project:
                update_run(run["id"], status="failed", error="Không tìm thấy dự án phim.")
                return pipeline_status(project_id)
            qc = get_qc_status()
            if not qc.get("configured"):
                update_run(run["id"], status="failed", error="QC provider available: false")
                return pipeline_status(project_id)
            scene = _next_scene(project, run)
            if not scene:
                update_run(run["id"], status="completed", current_scene_id=None)
                append_run_log(run["id"], {"action": "completed"})
                return pipeline_status(project_id)
            heartbeat_execution_lease(project_id, worker_id, run_id=run.get("id"), scene_id=scene.get("id"))
            try:
                await process_one_scene(project_id, scene, run)
            except Exception as exc:
                message = str(exc)[:2000]
                append_run_log(run["id"], {"action": "scene_failed", "scene_id": scene.get("id"), "error": message})
                update_run(run["id"], status="failed", error=message)
                return pipeline_status(project_id)
            run = get_active_run(project_id) or run
            if run.get("stop_after_current") or run.get("status") == "stopping":
                update_run(run["id"], status="stopped")
                append_run_log(run["id"], {"action": "stopped_after_scene", "scene_id": scene.get("id")})
                return pipeline_status(project_id)
    finally:
        if _PIPELINE_OWNERS.get(project_id) == worker_id:
            _PIPELINE_OWNERS.pop(project_id, None)
        release_execution_lease(project_id, worker_id)
