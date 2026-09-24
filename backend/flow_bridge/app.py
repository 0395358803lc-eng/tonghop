import asyncio
import hmac
import os
import re
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .browser import FlowBrowserError, flow_browser, validate_downloaded_image
from .config import DATA_DIR, LEGACY_MEDIA_DIRS, MEDIA_DIR, load_config
from .job_store import load_jobs, patch_job, put_job, recover_interrupted_jobs
from . import sessions as flow_sessions

app = FastAPI(title="TH Media Flow Bridge", version="0.4.0")

_jobs: dict[str, dict] = load_jobs()
_recovered_jobs = recover_interrupted_jobs(_jobs)
_tasks: set[asyncio.Task] = set()


def _media_roots(subdir: str | None = None) -> tuple[Path, ...]:
    roots = [MEDIA_DIR.resolve()]
    for legacy in LEGACY_MEDIA_DIRS:
        resolved = legacy.resolve()
        if resolved not in roots:
            roots.append(resolved)
    if subdir:
        return tuple((root / subdir).resolve() for root in roots)
    return tuple(roots)


def _is_allowed_media_path(path: Path, subdir: str) -> bool:
    resolved = path.resolve()
    for root in _media_roots():
        legacy = (root / subdir).resolve()
        if resolved.is_relative_to(legacy):
            return True
        projects = (root / "projects").resolve()
        if resolved.is_relative_to(projects):
            rel = resolved.relative_to(projects)
            if len(rel.parts) >= 2 and rel.parts[1] == subdir:
                return True
    return False


def require_bridge_key(authorization: str | None = Header(default=None)) -> None:
    expected = str(load_config().get("api_key") or "")
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Flow Bridge API key không hợp lệ.")


class SessionSaveIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)


class SessionNewIn(BaseModel):
    save_current: bool = True
    name: str | None = Field(default=None, max_length=120)


class VideoGenerationIn(BaseModel):
    project_id: str | None = None
    flow_project_id: str | None = None
    scene_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    prompt: str = Field(min_length=1, max_length=100000)
    model: str | None = None
    duration: float | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    reference_image_url: str | None = None
    previous_video_url: str | None = None
    previous_end_state: str | None = None
    start_state: str | None = None
    end_state: str | None = None
    resource_manifest: dict = Field(default_factory=dict)


class ImageGenerationIn(BaseModel):
    project_id: str | None = None
    flow_project_id: str | None = None
    asset_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    prompt: str = Field(min_length=1, max_length=100000)
    model: str | None = None
    aspect_ratio: str | None = None
    output_count: int = Field(default=1, ge=1, le=4)


def _numeric_resolution(value: str) -> int:
    try:
        return int(str(value).lower().replace("p", ""))
    except Exception:
        return 0


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "flow_image_download_auth_required" in message:
        return "FLOW_IMAGE_DOWNLOAD_AUTH_REQUIRED"
    if "flow_project_not_found" in message or "project_not_found" in message or "reason=project" in message:
        return "FLOW_PROJECT_NOT_FOUND"
    if "flow_image_invalid_response" in message:
        return "FLOW_IMAGE_INVALID_RESPONSE"
    if "session" in message or "đăng nhập" in message or "authenticated" in message:
        return "SESSION_EXPIRED"
    if "reference" in message or "ingredient" in message or "asset" in message:
        return "REFERENCE_ERROR"
    if "hết thời gian" in message or "timeout" in message or "time out" in message:
        return "GENERATION_TIMEOUT"
    if "audio_generation_failed" in message or "không tạo được audio" in message:
        return "AUDIO_GENERATION_FAILED"
    if (
        "flow_policy_blocked" in message
        or "vi phạm chính sách" in message
        or "người nổi tiếng" in message
        or "public figure" in message
        or "policy violation" in message
    ):
        return "FLOW_POLICY_BLOCKED"
    if "flow_generation_failed" in message:
        return "FLOW_GENERATION_FAILED"
    if "không đủ tín dụng" in message or "not enough credits" in message or "credits_insufficient" in message:
        return "FLOW_CREDITS_INSUFFICIENT"
    if "download" in message or "tải xuống" in message or "file video" in message:
        return "DOWNLOAD_ERROR"
    # UI/selector errors must be classified before generic model/capability words.
    # Otherwise "không tìm thấy selector model" is incorrectly reported as
    # CAPABILITY_MISMATCH and hides the real Flow DOM regression.
    if (
        "không tìm thấy nút" in message
        or "generate vẫn disabled" in message
        or "selector" in message
        or "flow_ui_not_ready" in message
        or "model_selection_mismatch" in message
        or "không hiển thị mode" in message
        or "không mở được panel cài đặt" in message
        or "ui" in message
    ):
        return "FLOW_UI_CHANGED"
    if "model" in message or "resolution" in message or "thời lượng" in message or "aspect" in message:
        return "CAPABILITY_MISMATCH"
    return "FLOW_RUNTIME_ERROR"


def _resolve_reference_path(reference_url: str, media_project_id: str | None = None):
    match = re.search(r"/api/flow/render/([0-9a-fA-F-]{36})/(?:last-frame|first-frame)$", reference_url or "")
    if not match:
        raise RuntimeError("Chỉ chấp nhận reference frame do TH Media Flow pipeline tạo.")
    job_id = match.group(1)
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise RuntimeError("Reference frame job ID không hợp lệ.") from exc
    folders: list[Path] = []
    for root in _media_roots():
        if media_project_id:
            folders.append(root / "projects" / media_project_id / "flow_downloads" / job_id)
        folders.append(root / "flow_downloads" / job_id)
        if not media_project_id:
            folders.extend(root.glob(f"projects/*/flow_downloads/{job_id}"))
    for folder in folders:
        candidates = sorted(folder.glob("last_frame_*.jpg"))
        if not candidates:
            candidates = sorted(folder.glob("first_frame_*.jpg"))
        if candidates:
            return candidates[0]
    raise RuntimeError("Không tìm thấy reference frame local cho scene trước.")


def _resolve_resource_reference_paths(manifest: dict) -> list[tuple[str, Path]]:
    if not isinstance(manifest, dict):
        return []
    missing = manifest.get("missing") or []
    if missing or manifest.get("ready") is False:
        raise RuntimeError("RESOURCE_LOCK_INCOMPLETE: " + ", ".join(str(x) for x in missing[:10]))

    resolved: list[tuple[str, Path]] = []
    for item in manifest.get("references") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("local_path") or "").strip()
        if not raw:
            continue
        path = Path(raw).resolve()
        if not _is_allowed_media_path(path, "film_assets"):
            raise RuntimeError("REFERENCE_ERROR: Canonical asset nằm ngoài các thư mục film_assets được phép.")
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"REFERENCE_ERROR: Canonical asset không tồn tại: {item.get('entity_id')}")
        kind = str(item.get("resource_type") or "other")
        resolved.append((kind, path))
    return resolved


async def _normalized_generation(body: VideoGenerationIn) -> dict:
    project_id = body.flow_project_id
    if not project_id:
        projects = await flow_browser.list_projects()
        if not projects:
            raise RuntimeError("Tài khoản Flow chưa có project.")
        project_id = projects[0]["id"]

    caps = await flow_browser.video_capabilities(project_id)
    aspects = caps.get("aspect_ratios") or ["16:9"]
    aspect_ratio = body.aspect_ratio if body.aspect_ratio in aspects else aspects[0]

    resolutions = caps.get("resolutions") or ["720p"]
    requested_resolution = body.resolution or "720p"
    resolution = requested_resolution if requested_resolution in resolutions else max(resolutions, key=_numeric_resolution)

    durations = [int(x) for x in (caps.get("durations") or [8])]
    requested_duration = int(round(body.duration or 8))
    duration = min(durations, key=lambda x: abs(x - requested_duration))

    return {
        "project_id": project_id,
        "model": body.model,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration": duration,
        "output_count": 1,
    }


async def _normalized_image_generation(body: ImageGenerationIn) -> dict:
    project_id = body.flow_project_id
    recovered_from_project_id = None

    if project_id:
        try:
            caps = await flow_browser.image_capabilities(project_id)
        except Exception as exc:
            if _classify_error(exc) != "FLOW_PROJECT_NOT_FOUND":
                raise
            recovered_from_project_id = project_id
            await flow_browser.restore_workspace(None)
            projects = await flow_browser.list_projects()
            if not projects:
                raise RuntimeError(
                    f"FLOW_PROJECT_NOT_FOUND: Flow project {project_id} không còn tồn tại và workspace không có project thay thế."
                ) from exc
            project_id = projects[0]["id"]
            caps = await flow_browser.image_capabilities(project_id)
    else:
        projects = await flow_browser.list_projects()
        if not projects:
            raise RuntimeError("FLOW_PROJECT_NOT_FOUND: Tài khoản Flow chưa có project.")
        project_id = projects[0]["id"]
        caps = await flow_browser.image_capabilities(project_id)

    aspects = caps.get("aspect_ratios") or ["1:1", "16:9", "9:16"]
    requested_aspect = re.sub(r"\s+", "", str(body.aspect_ratio or "1:1"))
    known = {re.sub(r"\s+", "", str(item)) for item in aspects}
    if requested_aspect in known or requested_aspect in {"1:1", "16:9", "9:16", "3:4", "4:3"}:
        aspect_ratio = requested_aspect
    else:
        aspect_ratio = aspects[0]

    models = caps.get("models") or ["Nano Banana 2"]
    model = body.model if body.model in models else ("Nano Banana 2" if "Nano Banana 2" in models else models[0])
    outputs = [int(x) for x in (caps.get("output_counts") or [1])]
    output_count = body.output_count if body.output_count in outputs else 1
    return {
        "project_id": project_id,
        "model": model,
        "aspect_ratio": aspect_ratio,
        "output_count": output_count,
        "project_recovered": recovered_from_project_id is not None,
        "recovered_from_project_id": recovered_from_project_id,
    }


async def _run_image_job(job_id: str, body: ImageGenerationIn) -> None:
    try:
        patch_job(_jobs, job_id, status="preparing", progress=5)
        effective = await _normalized_image_generation(body)
        patch_job(_jobs, job_id, status="generating", progress=20, effective_settings=effective)
        generate_kwargs = {
            key: effective[key]
            for key in ("project_id", "model", "aspect_ratio", "output_count")
            if key in effective and effective[key] is not None
        }
        result = await flow_browser.generate_image(
            job_id=job_id,
            prompt=body.prompt,
            media_project_id=body.project_id,
            timeout_seconds=600,
            **generate_kwargs,
        )
        patch_job(
            _jobs,
            job_id,
            status="completed",
            progress=100,
            result_url=f"/v1/generations/image/{job_id}/file",
            flow_project_id=result.get("project_id"),
            media_id=result.get("media_id"),
            source_url=result.get("source_url"),
            result_path=result.get("result_path"),
            model=result.get("model"),
            aspect_ratio=result.get("aspect_ratio"),
            mime_type=result.get("mime_type"),
            error=None,
        )
    except Exception as exc:
        patch_job(
            _jobs,
            job_id,
            status="failed",
            progress=100,
            error=str(exc)[:2000],
            error_code=_classify_error(exc),
        )


async def _run_video_job(job_id: str, body: VideoGenerationIn) -> None:
    job = _jobs[job_id]
    try:
        patch_job(_jobs, job_id, status="preparing", progress=5)
        canonical = _resolve_resource_reference_paths(body.resource_manifest)
        previous_path = None
        if body.reference_image_url:
            previous_path = _resolve_reference_path(body.reference_image_url, body.project_id)
        elif body.previous_video_url:
            raise RuntimeError("Scene trước có video nhưng chưa có last-frame reference hợp lệ.")

        characters = [path for kind, path in canonical if kind == "character"]
        locations = [path for kind, path in canonical if kind == "location"]
        props = [path for kind, path in canonical if kind == "prop"]
        priority = characters + ([previous_path] if previous_path else []) + locations + props
        max_refs = max(1, min(int(os.getenv("FLOW_MAX_REFERENCE_IMAGES", "4")), 8))
        reference_paths = []
        for path in priority:
            if path is not None and path not in reference_paths:
                reference_paths.append(path)
            if len(reference_paths) >= max_refs:
                break
        if not reference_paths:
            raise RuntimeError("RESOURCE_LOCK_INCOMPLETE: Scene không có canonical/boundary reference nào để khóa hình ảnh.")

        effective = await _normalized_generation(body)
        effective["reference_count"] = len(reference_paths)
        effective["reference_files"] = [path.name for path in reference_paths]
        patch_job(_jobs, job_id, status="generating", progress=20, effective_settings=effective)
        generate_kwargs = {
            key: effective[key]
            for key in ("project_id", "model", "aspect_ratio", "resolution", "duration", "output_count")
            if key in effective and effective[key] is not None
        }
        result = await flow_browser.generate_video(
            job_id=job_id,
            prompt=body.prompt,
            media_project_id=body.project_id,
            reference_image_paths=reference_paths,
            timeout_seconds=900,
            **generate_kwargs,
        )
        patch_job(
            _jobs,
            job_id,
            status="completed",
            progress=100,
            result_url=result.get("result_url"),
            first_frame_url=result.get("first_frame_url"),
            last_frame_url=result.get("last_frame_url"),
            flow_project_id=result.get("project_id"),
            error=None,
        )
    except Exception as exc:
        patch_job(
            _jobs,
            job_id,
            status="failed",
            progress=100,
            error=str(exc)[:2000],
            error_code=_classify_error(exc),
        )


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@app.get("/v1/health", dependencies=[Depends(require_bridge_key)])
async def health():
    cfg = load_config()
    return {
        "ok": True,
        "service": "TH Media Flow Bridge",
        "version": "0.4.0",
        "cdp_url": cfg["cdp_url"],
        "persisted_jobs": len(_jobs),
        "recovered_jobs": _recovered_jobs,
    }


@app.get("/v1/metrics", dependencies=[Depends(require_bridge_key)])
async def metrics():
    statuses: dict[str, int] = {}
    for job in _jobs.values():
        key = str(job.get("status") or "unknown")
        statuses[key] = statuses.get(key, 0) + 1
    video_files: list[Path] = []
    image_files: list[Path] = []
    for root in _media_roots():
        media_root = root / "flow_downloads"
        image_root = root / "flow_image_downloads"
        if media_root.exists():
            video_files.extend(media_root.glob("*/result.*"))
        if image_root.exists():
            image_files.extend(image_root.glob("*/result.*"))
        video_files.extend(root.glob("projects/*/flow_downloads/*/result.*"))
        image_files.extend(root.glob("projects/*/flow_image_downloads/*/result.*"))
    video_bytes = sum(path.stat().st_size for path in video_files if path.is_file())
    image_bytes = sum(path.stat().st_size for path in image_files if path.is_file())
    total_media_bytes = video_bytes + image_bytes
    latest = max((str(job.get("updated_at") or "") for job in _jobs.values()), default=None)
    return {
        "jobs_total": len(_jobs),
        "jobs_by_status": statuses,
        "active_jobs": sum(statuses.get(x, 0) for x in ("queued", "preparing", "generating")),
        "video_files": len(video_files),
        "video_bytes": video_bytes,
        "image_files": len(image_files),
        "image_bytes": image_bytes,
        "media_bytes": total_media_bytes,
        "latest_job_updated_at": latest,
    }


@app.get("/v1/session", dependencies=[Depends(require_bridge_key)])
async def session():
    return await flow_browser.session_status()


@app.post("/v1/session/restore-workspace", dependencies=[Depends(require_bridge_key)])
async def restore_workspace(project_id: str | None = None):
    return await flow_browser.restore_workspace(project_id)


@app.post("/v1/session/open-login", dependencies=[Depends(require_bridge_key)])
async def open_login():
    return await flow_browser.open_login_window()


def _reject_if_busy() -> None:
    busy = [job for job in _jobs.values() if str(job.get("status") or "") in {"queued", "preparing", "generating"}]
    if busy:
        raise HTTPException(409, "Đang có generation Flow chạy. Không thể đổi phiên lúc này.")


async def _with_chrome_swap(fn):
    _reject_if_busy()
    await flow_browser.close()
    try:
        return await asyncio.to_thread(fn)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/sessions", dependencies=[Depends(require_bridge_key)])
async def list_saved_sessions():
    data = flow_sessions.list_sessions()
    status = await flow_browser.session_status()
    data["authenticated"] = bool(status.get("authenticated"))
    data["session_state"] = status.get("state")
    if not data.get("active_account"):
        data["active_account"] = status.get("title")
    return data


@app.post("/v1/sessions/save", dependencies=[Depends(require_bridge_key)])
async def save_session(body: SessionSaveIn | None = None):
    payload = body or SessionSaveIn()
    result = await _with_chrome_swap(lambda: flow_sessions.save_current_session(payload.name))
    status = await flow_browser.session_status()
    result["authenticated"] = bool(status.get("authenticated"))
    return result


@app.post("/v1/sessions/new", dependencies=[Depends(require_bridge_key)])
async def create_new_session(body: SessionNewIn | None = None):
    payload = body or SessionNewIn()
    result = await _with_chrome_swap(lambda: flow_sessions.new_session(payload.save_current, payload.name))
    status = await flow_browser.session_status()
    result["authenticated"] = bool(status.get("authenticated"))
    return result


@app.post("/v1/sessions/{session_id}/restore", dependencies=[Depends(require_bridge_key)])
async def restore_saved_session(session_id: str):
    result = await _with_chrome_swap(lambda: flow_sessions.restore_session(session_id))
    status = await flow_browser.session_status()
    result["authenticated"] = bool(status.get("authenticated"))
    return result


@app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_bridge_key)])
async def remove_saved_session(session_id: str):
    return flow_sessions.delete_session(session_id)


@app.get("/v1/projects", dependencies=[Depends(require_bridge_key)])
async def projects():
    status = await flow_browser.session_status()
    if not status.get("authenticated"):
        raise HTTPException(409, status.get("reason") or "Flow session chưa đăng nhập.")
    items = await flow_browser.list_projects()
    return {"projects": items, "count": len(items)}


@app.get("/v1/capabilities/video", dependencies=[Depends(require_bridge_key)])
async def video_capabilities(project_id: str | None = None):
    status = await flow_browser.session_status()
    if not status.get("authenticated"):
        raise HTTPException(409, status.get("reason") or "Flow session chưa đăng nhập.")
    return await flow_browser.video_capabilities(project_id)


@app.get("/v1/capabilities/image", dependencies=[Depends(require_bridge_key)])
async def image_capabilities(project_id: str | None = None):
    status = await flow_browser.session_status()
    if not status.get("authenticated"):
        raise HTTPException(409, status.get("reason") or "Flow session chưa đăng nhập.")
    return await flow_browser.image_capabilities(project_id)


@app.post("/v1/generations/image", dependencies=[Depends(require_bridge_key)])
async def generate_image(body: ImageGenerationIn):
    status = await flow_browser.session_status()
    if not status.get("authenticated"):
        raise HTTPException(409, status.get("reason") or "Flow session chưa đăng nhập.")
    if body.idempotency_key:
        for existing in _jobs.values():
            if existing.get("idempotency_key") == body.idempotency_key:
                return {
                    "job_id": existing["job_id"],
                    "status": existing.get("status", "queued"),
                    "timeout_seconds": 600,
                    "deduplicated": True,
                }

    job_id = str(uuid.uuid4())
    put_job(_jobs, job_id, {
        "job_id": job_id,
        "job_type": "image",
        "status": "queued",
        "progress": 0,
        "asset_id": body.asset_id,
        "idempotency_key": body.idempotency_key,
        "project_id": body.project_id,
        "flow_project_id": body.flow_project_id,
        "result_url": None,
        "error": None,
    })
    _spawn(_run_image_job(job_id, body))
    return {"job_id": job_id, "status": "queued", "timeout_seconds": 600}


@app.get("/v1/generations/image/{job_id}/file", dependencies=[Depends(require_bridge_key)])
async def image_file(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Không tìm thấy Flow image job {job_id}.")
    raw = str(job.get("result_path") or "")
    if not raw:
        raise HTTPException(409, "Flow image job chưa có file kết quả.")
    path = Path(raw)
    if not _is_allowed_media_path(path, "flow_image_downloads"):
        raise HTTPException(400, "Đường dẫn Flow image không hợp lệ.")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File ảnh Flow không còn tồn tại.")
    try:
        info = validate_downloaded_image(path.read_bytes(), "")
    except FlowBrowserError as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(path, media_type=info["mime"], filename=path.name)


@app.post("/v1/generations/video", dependencies=[Depends(require_bridge_key)])
async def generate_video(body: VideoGenerationIn):
    status = await flow_browser.session_status()
    if not status.get("authenticated"):
        raise HTTPException(409, status.get("reason") or "Flow session chưa đăng nhập.")
    if body.idempotency_key:
        for existing in _jobs.values():
            if existing.get("idempotency_key") == body.idempotency_key:
                return {
                    "job_id": existing["job_id"],
                    "status": existing.get("status", "queued"),
                    "timeout_seconds": 900,
                    "deduplicated": True,
                }

    job_id = str(uuid.uuid4())
    put_job(_jobs, job_id, {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "scene_id": body.scene_id,
        "idempotency_key": body.idempotency_key,
        "project_id": body.project_id,
        "flow_project_id": body.flow_project_id,
        "result_url": None,
        "error": None,
    })
    _spawn(_run_video_job(job_id, body))
    return {"job_id": job_id, "status": "queued", "timeout_seconds": 900}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_bridge_key)])
async def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Không tìm thấy Flow job {job_id}.")
    return job


@app.post("/v1/runtime/stop-chrome", dependencies=[Depends(require_bridge_key)])
async def stop_runtime_chrome():
    await flow_browser.close()
    flow_sessions.stop_flow_chrome()
    return {"ok": True}


@app.on_event("shutdown")
async def shutdown():
    await flow_browser.close()
