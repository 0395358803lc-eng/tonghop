import os
import shutil
import socket
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from .chat_store import add_message, create_chat, delete_chat, get_chat, list_chats, update_chat
from .provider_store import delete_provider, get_provider, list_saved, save_provider
from .provider_health import validate_provider_credentials
from .providers.models import list_models
from .providers.registry import DEFAULT_MODELS, PROVIDERS
from .providers.service import run_chat
from .video_service import detect_platform, process_video_job
from .vision_service import delete_video_artifacts, get_frame_path
from .video_store import create_video_job, delete_video_job, get_video_job, list_video_jobs
from .video_proxy_store import delete_video_proxy, get_video_proxy_status, save_video_proxy, test_video_proxy
from .film_store import create_film_project, delete_film_project, get_film_project, list_film_projects, update_film_project, update_film_scene
from .film_service import audit_film_continuity, process_film_project
from .film_production_gate import auto_repair_project_derived, evaluate_production_gate_v2, run_production_gate
from .film_consistency_v2 import repair_consistency_v2, run_consistency_audit_v2
from .film_render_service import enqueue_render, pause_render_queue, process_render_queue, render_status, resume_render_queue, retry_render
from .film_pipeline_service import pause_pipeline, pipeline_status, pipeline_worker_active, process_pipeline, resume_pipeline, retry_scene, start_pipeline, stop_pipeline
from .film_scene_state_store import get_execution_lease, list_active_runs, list_candidates
from .film_dialogue_service import project_audio_requirements
from .film_voice_profile_store import ensure_voice_profiles, list_voice_profiles
from .film_speaker_identity import speaker_identity_status
from .film_speaker_acceptance import run_project_speaker_acceptance
from .desktop_shutdown import (
    prepare_force_shutdown,
    request_safe_shutdown,
    shutdown_status as desktop_shutdown_status,
)
from .desktop_diagnostics import diagnostics_status, export_diagnostics_bundle, validate_diagnostics_filename
from .desktop_update import create_desktop_backup, list_desktop_backups, prepare_update_backup, stage_desktop_restore, update_gate_status
from .desktop_resources import assert_render_resources, cleanup_temp, resource_status
from .desktop_network import internet_status
from .film_boundary_service import check_project_junctions, check_project_junctions_async, list_project_junctions, retry_junction
from .film_final_assembly import assemble_project, final_status
from .film_master_qc import run_master_qc
from .film_acceptance_snapshot import backfill_acceptance_snapshots, get_public_acceptance_snapshot, list_acceptance_snapshots
from .film_event_store import event_store_available, list_events
from .film_observability_service import project_metrics
from .film_recovery_service import detect_orphan_jobs, reconcile_project, recovery_state_clean
from .film_capability_matrix import list_capability_matrix, matrix_is_fresh, refresh_capability_matrix
from .film_canonical_service import DEFAULT_FLOW_IMAGE_MODEL, DEFAULT_IMAGE_MODEL, generate_project_canonical_assets, qc_existing_canonical_resource, qc_project_canonical_assets
from .film_resource_store import get_project_resource, list_project_resources, lock_project_resources, save_canonical_asset, sync_project_resources, update_resource_binding
from .config import DATA_DIR, MEDIA_DIR
from .flow_bridge_client import delete_flow_session, get_flow_image_capabilities, get_flow_metrics, get_flow_projects, get_flow_video_capabilities, list_flow_sessions, new_flow_session, open_flow_login, restore_flow_session, save_flow_session, test_flow_bridge
from .flow_store import delete_flow_settings, get_flow_status, save_flow_settings
from .film_media_store import assert_media_id, get_media, list_media_versions, media_roots, public_media, validate_media_path
from .film_media_service import delete_project_media_files, list_public_media, select_production_media
from .schemas import ChatCreate, ChatUpdate, MessageCreate, ProviderKeyIn, VideoAnalyzeIn, VideoProxyIn, FilmProjectCreate, FilmProjectUpdate, FilmSceneUpdate, FilmRenderQueueIn, FilmPipelineStartIn, FilmCanonicalGenerateIn, FilmCanonicalQcIn, FilmResourceAssetIn, FilmResourceBindingIn, FlowBridgeSettingsIn, FlowSessionNewIn, FlowSessionSaveIn

router = APIRouter(prefix="/api")

@router.get("/health")
def health():
    return {"ok": True, "service": "TH Media"}


@router.get("/desktop/diagnostics")
async def desktop_diagnostics():
    return await diagnostics_status()


@router.post("/desktop/diagnostics/export")
async def desktop_diagnostics_export():
    path = await export_diagnostics_bundle()
    return {"filename": path.name, "size_bytes": path.stat().st_size}


@router.get("/desktop/network")
def desktop_network_status():
    return internet_status()


@router.get("/desktop/resources")
def desktop_resources_status():
    return resource_status()


@router.post("/desktop/resources/cleanup-temp")
def desktop_resources_cleanup_temp():
    return cleanup_temp()


@router.get("/desktop/backups")
def desktop_backups_list():
    return list_desktop_backups()


@router.post("/desktop/backups")
def desktop_backups_create():
    return create_desktop_backup("user")


@router.post("/desktop/backups/{backup_name}/restore-stage")
def desktop_backup_restore_stage(backup_name: str):
    try:
        return stage_desktop_restore(backup_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/desktop/update/status")
def desktop_update_status():
    return update_gate_status()


@router.post("/desktop/update/prepare")
def desktop_update_prepare():
    result = prepare_update_backup()
    if not result.get("prepared"):
        raise HTTPException(status_code=409, detail="Pipeline đang chạy. Không thể cập nhật lúc này.")
    return result


@router.get("/desktop/diagnostics/export/{filename}")
def desktop_diagnostics_export_file(filename: str):
    try:
        path = validate_diagnostics_filename(filename)
    except ValueError as exc:
        raise HTTPException(404, "Không tìm thấy gói chẩn đoán.") from exc
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/ready")
async def ready():
    db_ok = True
    try:
        from .db import connect
        with connect() as conn:
            conn.execute("SELECT 1")
    except Exception:
        db_ok = False
    events_ok = event_store_available()
    flow = get_flow_status()
    flow_auth = False
    flow_payload = None
    flow_port = int(os.getenv("TH_MEDIA_FLOW_BRIDGE_PORT", "0") or 0)
    flow_runtime_active = False
    if flow_port:
        try:
            with socket.create_connection(("127.0.0.1", flow_port), timeout=0.08):
                flow_runtime_active = True
        except OSError:
            flow_runtime_active = False
    if flow_runtime_active:
        try:
            flow_payload = await test_flow_bridge()
            session = (flow_payload or {}).get("session") or {}
            flow_auth = bool((flow_payload or {}).get("authenticated") or session.get("authenticated") or (flow_payload or {}).get("ok"))
        except Exception:
            flow_payload = None
    speaker = speaker_identity_status()
    speaker_ok = bool(speaker.get("enabled") and speaker.get("model_exists"))
    checks = {
        "db": db_ok,
        "event_store": events_ok,
        "flow_configured": bool(flow.get("configured")),
        "flow_runtime_active": flow_runtime_active,
        "flow_authenticated": flow_auth,
        "capability_matrix_fresh": matrix_is_fresh("video"),
        "speaker_verifier_ready": speaker_ok,
    }
    ready_ok = bool(
        db_ok
        and events_ok
        and (speaker_ok or not speaker.get("required"))
    )

    storage = None
    try:
        usage = shutil.disk_usage(DATA_DIR)
        storage = {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0.0,
        }
    except Exception:
        storage = None

    configured_providers = sorted(list(list_saved().keys()))
    video_models = sorted({
        str(item.get("model") or "").strip()
        for item in list_capability_matrix(media_type="video")
        if str(item.get("model") or "").strip()
        and str(item.get("model") or "").strip().lower() != "unknown"
    })
    image_models = sorted({
        str(item.get("model") or "").strip()
        for item in list_capability_matrix(media_type="image")
        if str(item.get("model") or "").strip()
        and str(item.get("model") or "").strip().lower() != "unknown"
    })
    return {
        "ok": ready_ok,
        "ready": ready_ok,
        "health": True,
        "checks": checks,
        "flow": flow_payload,
        "speaker_identity": speaker,
        "runtime": {
            "desktop_mode": os.getenv("TH_MEDIA_DESKTOP_MODE") == "1",
            "active_pipelines": len(list_active_runs()),
            "configured_providers": configured_providers,
            "configured_provider_count": len(configured_providers),
            "ports": {
                "backend": int(os.getenv("TH_MEDIA_BACKEND_PORT", "0") or 0),
                "flow_bridge": int(os.getenv("TH_MEDIA_FLOW_BRIDGE_PORT", "0") or 0),
                "chrome_cdp": int(os.getenv("TH_MEDIA_CDP_PORT", "0") or 0),
            },
        },
        "capabilities": {
            "video_models": video_models,
            "image_models": image_models,
        },
        "storage": storage,
    }


@router.get("/flow")
def flow_status():
    return get_flow_status()


def _flow_render_folder(job_id: str):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(404, "Flow render job không hợp lệ.") from exc
    for root in media_roots():
        legacy = root / "flow_downloads" / job_id
        if legacy.exists():
            return legacy
        matches = list(root.glob(f"projects/*/flow_downloads/{job_id}"))
        if matches:
            return matches[0]
    return MEDIA_DIR / "flow_downloads" / job_id


@router.get("/flow/render/{job_id}/file")
def flow_render_file(job_id: str):
    folder = _flow_render_folder(job_id)
    candidates = [p for p in folder.glob("result.*") if p.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}]
    if not candidates:
        raise HTTPException(404, "Video Flow chưa sẵn sàng.")
    path = candidates[0]
    media_type = "video/mp4" if path.suffix.lower() in {".mp4", ".m4v"} else "video/webm" if path.suffix.lower() == ".webm" else "video/quicktime"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/flow/render/{job_id}/first-frame")
def flow_first_frame(job_id: str):
    candidates = sorted(_flow_render_folder(job_id).glob("first_frame_*.jpg"))
    if not candidates:
        raise HTTPException(404, "First frame chưa sẵn sàng.")
    return FileResponse(candidates[0], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/flow/render/{job_id}/last-frame")
def flow_last_frame(job_id: str):
    candidates = sorted(_flow_render_folder(job_id).glob("last_frame_*.jpg"))
    if not candidates:
        raise HTTPException(404, "Last frame chưa sẵn sàng.")
    return FileResponse(candidates[0], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.put("/flow")
def put_flow_settings(body: FlowBridgeSettingsIn):
    try:
        return save_flow_settings(body.bridge_url, body.api_key, body.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/flow")
def remove_flow_settings():
    delete_flow_settings()
    return {"ok": True}


@router.post("/flow/test")
async def check_flow_connection():
    try:
        return await test_flow_bridge()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không kết nối được Flow Bridge: {exc}") from exc


@router.post("/flow/open-login")
async def flow_open_login():
    try:
        return await open_flow_login()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không mở được Chrome Flow login: {exc}") from exc


def _flow_http_error(exc: httpx.HTTPStatusError, fallback: str):
    detail = fallback
    try:
        payload = exc.response.json()
        detail = payload.get("detail") or fallback
    except Exception:
        pass
    raise HTTPException(exc.response.status_code, str(detail))


@router.get("/flow/sessions")
async def flow_sessions():
    try:
        return await list_flow_sessions()
    except httpx.HTTPStatusError as exc:
        _flow_http_error(exc, "Không đọc được danh sách phiên Flow.")
    except Exception as exc:
        raise HTTPException(502, f"Không đọc được danh sách phiên Flow: {exc}") from exc


@router.post("/flow/sessions/save")
async def flow_save_session(body: FlowSessionSaveIn | None = None):
    payload = body or FlowSessionSaveIn()
    try:
        return await save_flow_session(payload.name)
    except httpx.HTTPStatusError as exc:
        _flow_http_error(exc, "Không lưu được phiên Flow.")
    except Exception as exc:
        raise HTTPException(502, f"Không lưu được phiên Flow: {exc}") from exc


@router.post("/flow/sessions/new")
async def flow_new_session(body: FlowSessionNewIn | None = None):
    payload = body or FlowSessionNewIn()
    try:
        return await new_flow_session(payload.save_current, payload.name)
    except httpx.HTTPStatusError as exc:
        _flow_http_error(exc, "Không tạo được phiên Flow mới.")
    except Exception as exc:
        raise HTTPException(502, f"Không tạo được phiên Flow mới: {exc}") from exc


@router.post("/flow/sessions/{session_id}/restore")
async def flow_restore_session(session_id: str):
    try:
        return await restore_flow_session(session_id)
    except httpx.HTTPStatusError as exc:
        _flow_http_error(exc, "Không chuyển được phiên Flow.")
    except Exception as exc:
        raise HTTPException(502, f"Không chuyển được phiên Flow: {exc}") from exc


@router.delete("/flow/sessions/{session_id}")
async def flow_delete_session(session_id: str):
    try:
        return await delete_flow_session(session_id)
    except httpx.HTTPStatusError as exc:
        _flow_http_error(exc, "Không xóa được phiên Flow.")
    except Exception as exc:
        raise HTTPException(502, f"Không xóa được phiên Flow: {exc}") from exc


@router.get("/flow/metrics")
async def flow_metrics():
    try:
        return await get_flow_metrics()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không đọc được Flow metrics: {exc}") from exc


@router.get("/flow/projects")
async def flow_projects():
    try:
        return await get_flow_projects()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không đọc được Flow projects: {exc}") from exc


@router.get("/flow/capabilities/video")
async def flow_video_capabilities(project_id: str | None = None):
    try:
        return await get_flow_video_capabilities(project_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không đọc được Flow video capabilities: {exc}") from exc


@router.get("/flow/capabilities/image")
async def flow_image_capabilities(project_id: str | None = None):
    try:
        return await get_flow_image_capabilities(project_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Flow Bridge trả HTTP {exc.response.status_code}.") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không đọc được Flow image capabilities: {exc}") from exc


@router.get("/providers")
def providers():
    saved = list_saved()
    result = []
    for key, cfg in PROVIDERS.items():
        item = {"id": key, **cfg, "configured": key in saved}
        item["masked_key"] = saved.get(key, {}).get("masked_key")
        item["custom_base_url"] = saved.get(key, {}).get("base_url")
        result.append(item)
    return result

@router.put("/providers/{provider}")
async def put_provider(provider: str, body: ProviderKeyIn):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    try:
        check = await validate_provider_credentials(provider, body.api_key.strip(), body.base_url)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    save_provider(provider, body.api_key, body.base_url)
    return {"ok": True, **check}

@router.post("/providers/{provider}/test")
async def test_provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    saved = get_provider(provider)
    if not saved:
        raise HTTPException(400, "Chưa lưu API key cho nhà cung cấp này")
    try:
        return await validate_provider_credentials(provider, saved["api_key"], saved.get("base_url"))
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

@router.delete("/providers/{provider}")
def remove_provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    delete_provider(provider)
    return {"ok": True}

@router.get("/providers/{provider}/models")
async def provider_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    saved = get_provider(provider)
    if not saved and provider != "xkiro":
        return {"models": DEFAULT_MODELS.get(provider, []), "configured": False}
    cfg = saved or {"api_key": None, "base_url": None}
    models = await list_models(provider, cfg["api_key"], cfg.get("base_url"))
    return {"models": models, "configured": bool(saved)}

@router.get("/film/projects")
def film_projects():
    return list_film_projects()

@router.post("/film/projects")
def new_film_project(body: FilmProjectCreate):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    if not get_provider(body.provider):
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")
    return create_film_project(body.name, body.original_text, body.provider, body.model, body.settings)

@router.get("/film/projects/{project_id}")
def film_project_detail(project_id: str):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.patch("/film/projects/{project_id}")
def patch_film_project(project_id: str, body: FilmProjectUpdate):
    project = update_film_project(project_id, body.name, body.settings)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.delete("/film/projects/{project_id}")
def remove_film_project(project_id: str, delete_media: bool = Query(default=False)):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    media_result = None
    if delete_media:
        media_result = delete_project_media_files(project_id)
    delete_film_project(project_id)
    return {"ok": True, "delete_media": delete_media, "media": media_result}

@router.post("/film/projects/{project_id}/analyze")
def analyze_film_project(project_id: str, background_tasks: BackgroundTasks):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    if project["status"] == "analyzing":
        return project
    background_tasks.add_task(process_film_project, project_id)
    return {**project, "status": "queued", "stage": "Đang xếp hàng phân tích", "progress": 1}

@router.patch("/film/projects/{project_id}/scenes/{scene_id}")
def patch_film_scene(project_id: str, scene_id: str, body: FilmSceneUpdate):
    try:
        project = update_film_scene(project_id, scene_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.post("/film/projects/{project_id}/continuity-check")
async def continuity_check(project_id: str):
    project = audit_film_continuity(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    sync_project_resources(project_id, "flow")
    try:
        await run_consistency_audit_v2(project_id, persist=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return get_film_project(project_id)


@router.get("/film/projects/{project_id}/consistency")
def film_consistency_status(project_id: str):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project.get("consistency_report") or {}


@router.post("/film/projects/{project_id}/consistency/repair")
async def repair_film_consistency(project_id: str):
    try:
        return await repair_consistency_v2(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/production-gate")
def film_production_gate_status(project_id: str):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project.get("production_gate") or {}


@router.post("/film/projects/{project_id}/production-gate")
def run_film_production_gate(project_id: str):
    try:
        report = run_production_gate(project_id, persist=True)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"production_gate": report, "project": get_film_project(project_id)}


@router.post("/film/projects/{project_id}/auto-repair")
def auto_repair_film_project(project_id: str):
    try:
        return auto_repair_project_derived(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/resources")
def film_resources(project_id: str, provider: str = "flow"):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return {"provider": provider, "resources": list_project_resources(project_id, provider)}


@router.post("/film/projects/{project_id}/resources/sync")
def sync_film_resources(project_id: str, provider: str = "flow"):
    try:
        resources = sync_project_resources(project_id, provider)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"provider": provider, "resources": resources}


@router.post("/film/projects/{project_id}/resources/generate")
def generate_film_resources(project_id: str, body: FilmCanonicalGenerateIn, background_tasks: BackgroundTasks):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    if body.resource_type and body.resource_type not in {"character", "location", "prop"}:
        raise HTTPException(400, "Resource type không hợp lệ")
    provider = str(body.provider or "xkiro").strip().lower()
    if provider not in {"xkiro", "flow"}:
        raise HTTPException(400, "Provider tạo ảnh phải là xkiro hoặc flow")
    model = body.model or (DEFAULT_FLOW_IMAGE_MODEL if provider == "flow" else DEFAULT_IMAGE_MODEL)
    resources = sync_project_resources(project_id, "flow")
    wanted = set(body.entity_ids or [])
    selected = [
        item for item in resources
        if item.get("status") not in {"retired", "locked"}
        and (bool(wanted) or item.get("status") in {"pending", "stale", "error"})
        and (not body.resource_type or item.get("resource_type") == body.resource_type)
        and (not wanted or item.get("entity_id") in wanted)
    ]
    for item in selected:
        update_resource_binding(
            project_id,
            str(item["resource_type"]),
            str(item["entity_id"]),
            provider="flow",
            status="pending",
            error=None,
            metadata_patch={
                "generation_status": "queued",
                "generation_provider": provider,
                "generation_model": model,
            },
        )
    background_tasks.add_task(
        generate_project_canonical_assets,
        project_id,
        resource_type=body.resource_type,
        entity_ids=body.entity_ids,
        provider=provider,
        model=model,
    )
    return {
        "accepted": len(selected),
        "provider": provider,
        "model": model,
        "resources": list_project_resources(project_id, "flow"),
    }


@router.post("/film/projects/{project_id}/resources/qc")
def qc_film_resources(project_id: str, body: FilmCanonicalQcIn, background_tasks: BackgroundTasks):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    if body.resource_type and body.resource_type not in {"character", "location", "prop"}:
        raise HTTPException(400, "Resource type không hợp lệ")
    repair_provider = str(body.repair_provider or "flow").strip().lower()
    if repair_provider not in {"flow", "xkiro"}:
        raise HTTPException(400, "repair_provider phải là flow hoặc xkiro")
    repair_model = body.repair_model or (DEFAULT_FLOW_IMAGE_MODEL if repair_provider == "flow" else DEFAULT_IMAGE_MODEL)
    background_tasks.add_task(
        qc_project_canonical_assets,
        project_id,
        resource_type=body.resource_type,
        entity_ids=body.entity_ids,
        auto_repair=body.auto_repair,
        repair_provider=repair_provider,
        repair_model=repair_model,
    )
    return {
        "accepted": True,
        "auto_repair": body.auto_repair,
        "repair_provider": repair_provider,
        "repair_model": repair_model,
        "resources": list_project_resources(project_id, "flow"),
    }


@router.put("/film/projects/{project_id}/resources/{resource_type}/{entity_id}/asset")
def put_film_resource_asset(project_id: str, resource_type: str, entity_id: str, body: FilmResourceAssetIn, background_tasks: BackgroundTasks, provider: str = "flow"):
    try:
        resource = save_canonical_asset(project_id, resource_type, entity_id, body.data_url, body.filename, provider, status="pending")
        background_tasks.add_task(
            qc_existing_canonical_resource,
            project_id,
            resource_type,
            entity_id,
            auto_repair=False,
        )
        return resource
    except ValueError as exc:
        raise HTTPException(409 if "LOCKED" in str(exc) else 400, str(exc)) from exc


@router.get("/film/projects/{project_id}/resources/{resource_type}/{entity_id}/asset")
def get_film_resource_asset(project_id: str, resource_type: str, entity_id: str, provider: str = "flow"):
    resource = get_project_resource(project_id, resource_type, entity_id, provider)
    if not resource or not resource.get("local_path"):
        raise HTTPException(404, "Tài nguyên chưa có ảnh canonical.")
    path = Path(str(resource["local_path"]))
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File canonical không còn trên máy.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


@router.post("/film/projects/{project_id}/resources/lock")
def lock_film_resources(project_id: str, provider: str = "flow"):
    try:
        resources = lock_project_resources(project_id, provider)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    missing = [item for item in resources if item.get("status") not in {"locked", "retired"}]
    return {"provider": provider, "locked": len(resources) - len(missing), "missing": missing, "resources": resources}


@router.patch("/film/projects/{project_id}/resources/{resource_type}/{entity_id}")
def patch_film_resource(project_id: str, resource_type: str, entity_id: str, body: FilmResourceBindingIn, provider: str = "flow"):
    if resource_type not in {"character", "location", "prop"}:
        raise HTTPException(400, "Resource type không hợp lệ")
    try:
        return update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider=provider,
            provider_ref=body.provider_ref,
            status=body.status,
            error=body.error,
            metadata_patch=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/film/projects/{project_id}/render")
def film_render_status(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return render_status(project_id)

@router.post("/film/projects/{project_id}/render/queue")
def queue_film_render(project_id: str, body: FilmRenderQueueIn, background_tasks: BackgroundTasks):
    try:
        assert_render_resources()
        jobs = enqueue_render(project_id, body.scene_ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    background_tasks.add_task(process_render_queue, project_id)
    return {"jobs": jobs, **render_status(project_id)}

@router.post("/film/projects/{project_id}/render/pause")
def pause_film_render(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    pause_render_queue(project_id)
    return render_status(project_id)

@router.post("/film/projects/{project_id}/render/resume")
def resume_film_render(project_id: str, background_tasks: BackgroundTasks):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    resume_render_queue(project_id)
    background_tasks.add_task(process_render_queue, project_id)
    return render_status(project_id)

@router.post("/film/render/jobs/{job_id}/retry")
def retry_film_render(job_id: str, background_tasks: BackgroundTasks):
    job = retry_render(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy render job")
    background_tasks.add_task(process_render_queue, job["project_id"])
    return job


@router.get("/film/projects/{project_id}/pipeline")
def film_pipeline_status(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return pipeline_status(project_id)


@router.get("/film/projects/{project_id}/pipeline/gate")
def film_pipeline_gate(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return evaluate_production_gate_v2(project_id)


@router.get("/film/projects/{project_id}/pipeline/ledger")
def film_pipeline_ledger(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    data = pipeline_status(project_id)
    return {"project_id": project_id, "ledgers": data.get("ledgers") or [], "scenes": data.get("scenes") or []}


@router.get("/film/projects/{project_id}/audio/requirements")
def film_audio_requirements(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return project_audio_requirements(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/voice-profiles")
def film_voice_profiles(project_id: str):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    profiles = ensure_voice_profiles(project)
    return {"project_id": project_id, "profiles": profiles or list_voice_profiles(project_id)}


@router.get("/film/speaker/status")
def film_speaker_status():
    return speaker_identity_status()


@router.post("/film/projects/{project_id}/speaker/acceptance")
def film_speaker_acceptance(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return run_project_speaker_acceptance(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/junctions")
def film_junctions(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return list_project_junctions(project_id)


@router.post("/film/projects/{project_id}/junctions/check")
async def film_junctions_check(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        items = await check_project_junctions_async(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"project_id": project_id, "junctions": items}


@router.post("/film/projects/{project_id}/junctions/{junction_id}/retry")
def film_junction_retry(project_id: str, junction_id: str, background_tasks: BackgroundTasks):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        updated = retry_junction(project_id, junction_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not pipeline_worker_active(project_id):
        background_tasks.add_task(process_pipeline, project_id)
    return updated


@router.get("/film/projects/{project_id}/final/status")
def film_final_status(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return final_status(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/film/projects/{project_id}/final/assemble")
def film_final_assemble(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return assemble_project(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/film/projects/{project_id}/final/qc")
def film_final_qc(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return run_master_qc(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/final/qc")
def film_final_qc_status(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    try:
        return final_status(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/scenes/{scene_id}/acceptance-snapshots")
def film_scene_snapshots(project_id: str, scene_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    items = list_acceptance_snapshots(project_id, scene_id)
    return {
        "project_id": project_id,
        "scene_id": scene_id,
        "snapshots": [get_public_acceptance_snapshot(project_id, item["id"]) for item in items],
    }


@router.get("/film/projects/{project_id}/scenes/{scene_id}/acceptance-snapshots/{snapshot_id}")
def film_scene_snapshot(project_id: str, scene_id: str, snapshot_id: str):
    row = get_public_acceptance_snapshot(project_id, snapshot_id)
    if not row or row.get("scene_id") != scene_id:
        raise HTTPException(404, "Không tìm thấy snapshot")
    return row


@router.post("/film/projects/{project_id}/acceptance-snapshots/backfill")
def film_snapshot_backfill(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return backfill_acceptance_snapshots(project_id)


@router.get("/desktop/shutdown/status")
def desktop_shutdown_state():
    return desktop_shutdown_status()


@router.post("/desktop/shutdown/request-safe")
def desktop_shutdown_request_safe():
    return request_safe_shutdown()


@router.post("/desktop/shutdown/prepare-force")
def desktop_shutdown_prepare_force():
    return prepare_force_shutdown()


@router.post("/film/projects/{project_id}/pipeline/start")
def film_pipeline_start(project_id: str, body: FilmPipelineStartIn, background_tasks: BackgroundTasks):
    try:
        assert_render_resources()
        status = start_pipeline(project_id, from_scene_id=body.from_scene_id, scene_limit=body.scene_limit)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not pipeline_worker_active(project_id):
        background_tasks.add_task(process_pipeline, project_id)
    return status


@router.post("/film/projects/{project_id}/pipeline/pause")
def film_pipeline_pause(project_id: str):
    try:
        return pause_pipeline(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/film/projects/{project_id}/pipeline/resume")
def film_pipeline_resume(project_id: str, background_tasks: BackgroundTasks):
    try:
        status = resume_pipeline(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not pipeline_worker_active(project_id):
        background_tasks.add_task(process_pipeline, project_id)
    return status


@router.post("/film/projects/{project_id}/pipeline/stop")
def film_pipeline_stop(project_id: str):
    try:
        return stop_pipeline(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/film/projects/{project_id}/pipeline/candidates")
def film_pipeline_candidates(project_id: str, scene_id: str | None = None):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    if scene_id:
        return {"project_id": project_id, "scene_id": scene_id, "candidates": list_candidates(project_id, scene_id)}
    scenes = pipeline_status(project_id).get("scenes") or []
    items = []
    for scene in scenes:
        items.extend(list_candidates(project_id, scene.get("scene_id")))
    return {"project_id": project_id, "candidates": items}


@router.post("/film/projects/{project_id}/pipeline/retry/{scene_id}")
def film_pipeline_retry(project_id: str, scene_id: str, background_tasks: BackgroundTasks):
    try:
        status = retry_scene(project_id, scene_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not pipeline_worker_active(project_id):
        background_tasks.add_task(process_pipeline, project_id)
    return status


def _require_film_project(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")


@router.get("/film/projects/{project_id}/events")
def film_project_events(
    project_id: str,
    run_id: str | None = None,
    scene_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = 200,
    offset: int = 0,
):
    _require_film_project(project_id)
    items = list_events(
        project_id, run_id=run_id, scene_id=scene_id, event_type=event_type,
        severity=severity, from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset,
    )
    return {"project_id": project_id, "events": items, "count": len(items)}


@router.get("/film/projects/{project_id}/runs/{run_id}/events")
def film_run_events(project_id: str, run_id: str, scene_id: str | None = None, event_type: str | None = None, limit: int = 200):
    _require_film_project(project_id)
    items = list_events(project_id, run_id=run_id, scene_id=scene_id, event_type=event_type, limit=limit)
    return {"project_id": project_id, "run_id": run_id, "events": items, "count": len(items)}


@router.get("/film/projects/{project_id}/metrics")
def film_project_metrics(project_id: str, run_id: str | None = None):
    _require_film_project(project_id)
    return project_metrics(project_id, run_id=run_id)


@router.get("/film/projects/{project_id}/recovery")
def film_recovery_status(project_id: str):
    _require_film_project(project_id)
    orphans = detect_orphan_jobs(project_id)
    return {
        "project_id": project_id,
        "lease": get_execution_lease(project_id),
        "orphans": [{"id": item.get("id"), "scene_id": item.get("scene_id"), "status": item.get("status")} for item in orphans],
        "recovery_state_clean": recovery_state_clean(project_id),
        "event_store_available": event_store_available(),
    }


@router.post("/film/projects/{project_id}/recovery/reconcile")
def film_recovery_reconcile(project_id: str):
    _require_film_project(project_id)
    return reconcile_project(project_id)


@router.get("/film/capabilities/matrix")
def film_capability_matrix_get(media_type: str | None = None):
    rows = list_capability_matrix(media_type=media_type)
    return {"provider": "flow", "fresh": matrix_is_fresh(media_type or "video"), "items": rows, "count": len(rows)}


@router.post("/film/capabilities/matrix")
async def film_capability_matrix_refresh(project_id: str | None = None):
    return await refresh_capability_matrix(project_id)


def _require_media(media_id: str):
    try:
        media_id = assert_media_id(media_id)
    except ValueError as exc:
        raise HTTPException(404, "Media không hợp lệ.") from exc
    item = get_media(media_id)
    if not item:
        raise HTTPException(404, "Không tìm thấy media.")
    return item


def _media_file_response(path: Path, mime: str, filename: str, range_header: str | None = None):
    file_size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=0"}
    if not range_header or not range_header.lower().startswith("bytes="):
        return FileResponse(
            path,
            media_type=mime,
            filename=filename,
            content_disposition_type="inline",
            headers=headers,
        )
    spec = range_header.split("=", 1)[1].split(",")[0].strip()
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        raise HTTPException(416, "Range không hợp lệ.")
    try:
        if start_s == "" and end_s:
            start = max(file_size - int(end_s), 0)
            end = file_size - 1
        else:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
    except ValueError as exc:
        raise HTTPException(416, "Range không hợp lệ.") from exc
    start = max(0, start)
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        raise HTTPException(
            416,
            "Range không hợp lệ.",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    length = end - start + 1

    def iterator():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers.update({
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{filename}"',
    })
    return StreamingResponse(iterator(), status_code=206, media_type=mime, headers=headers)


@router.get("/film/projects/{project_id}/media")
def film_project_media(
    project_id: str,
    scene_id: str | None = None,
    media_type: str | None = None,
    role: str | None = None,
    status: str | None = None,
    selected_only: bool = False,
):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    items = list_public_media(
        project_id,
        scene_id=scene_id,
        media_type=media_type,
        role=role,
        status=status,
        selected_only=selected_only,
    )
    return {"project_id": project_id, "count": len(items), "media": items}


@router.get("/film/projects/{project_id}/scenes/{scene_id}/media")
def film_scene_media(project_id: str, scene_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    items = list_public_media(project_id, scene_id=scene_id)
    return {"project_id": project_id, "scene_id": scene_id, "count": len(items), "media": items}


@router.get("/film/media/{media_id}")
def film_media_detail(media_id: str):
    return public_media(_require_media(media_id))


@router.get("/film/media/{media_id}/file")
def film_media_file(media_id: str, request: Request):
    item = _require_media(media_id)
    try:
        path = validate_media_path(item.get("file_path"))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    mime = item.get("mime_type") or ("video/mp4" if item.get("media_type") == "video" else "image/jpeg")
    filename = Path(str((item.get("metadata") or {}).get("download_name") or path.name)).name
    return _media_file_response(path, mime, filename, request.headers.get("range"))


@router.get("/film/media/{media_id}/thumbnail")
def film_media_thumbnail(media_id: str):
    item = _require_media(media_id)
    thumb = item.get("thumbnail_path") or (item.get("file_path") if item.get("media_type") == "image" else None)
    try:
        path = validate_media_path(thumb)
    except ValueError as exc:
        raise HTTPException(404, "Thumbnail chưa sẵn sàng.") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/film/media/{media_id}/select")
def film_media_select(media_id: str):
    _require_media(media_id)
    try:
        selected = select_production_media(media_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return public_media(selected)


@router.get("/film/media/{media_id}/versions")
def film_media_versions(media_id: str):
    current = _require_media(media_id)
    versions = [public_media(item) for item in list_media_versions(current["id"])]
    return {"media_id": current["id"], "output_key": current["output_key"], "count": len(versions), "versions": versions}

@router.get("/video/proxy")
def video_proxy_status():
    return get_video_proxy_status()

@router.put("/video/proxy")
def put_video_proxy(body: VideoProxyIn):
    try:
        proxy = save_video_proxy(body.proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = get_video_proxy_status()
    result["ok"] = True
    return result

@router.delete("/video/proxy")
def remove_video_proxy():
    delete_video_proxy()
    return {"ok": True}

@router.post("/video/proxy/test")
async def check_video_proxy():
    try:
        return await test_video_proxy()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@router.get("/video/jobs")
def video_jobs():
    return list_video_jobs()

@router.post("/video/jobs")
async def new_video_job(body: VideoAnalyzeIn, background_tasks: BackgroundTasks):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    if not get_provider(body.provider):
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")
    try:
        platform = detect_platform(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = create_video_job(body.url.strip(), platform, body.provider, body.model)
    background_tasks.add_task(process_video_job, job["id"])
    return job

@router.get("/video/jobs/{job_id}")
def video_job(job_id: str):
    job = get_video_job(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy tác vụ phân tích")
    return job

@router.get("/video/jobs/{job_id}/frames/{filename}")
def video_frame(job_id: str, filename: str):
    if not get_video_job(job_id):
        raise HTTPException(404, "Không tìm thấy tác vụ phân tích")
    path = get_frame_path(job_id, filename)
    if not path:
        raise HTTPException(404, "Không tìm thấy keyframe")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

@router.delete("/video/jobs/{job_id}")
def remove_video_job(job_id: str):
    delete_video_artifacts(job_id)
    delete_video_job(job_id)
    return {"ok": True}

@router.get("/chats")
def chats():
    return list_chats()

@router.post("/chats")
def new_chat(body: ChatCreate):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    return create_chat(body.provider, body.model)

@router.get("/chats/{chat_id}")
def chat_detail(chat_id: str):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    return chat

@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, body: ChatUpdate):
    chat = update_chat(chat_id, **body.model_dump())
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    return chat

@router.delete("/chats/{chat_id}")
def remove_chat(chat_id: str):
    delete_chat(chat_id)
    return {"ok": True}

@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageCreate):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    credentials = get_provider(chat["provider"])
    if not credentials:
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")

    text = body.content.strip()
    current = get_chat(chat_id)
    context = [{"role": m["role"], "content": m["content"]} for m in current["messages"]]
    context.append({"role": "user", "content": text})
    try:
        answer = await run_chat(chat["provider"], credentials["api_key"], credentials.get("base_url"), chat["model"], context)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        name = PROVIDERS.get(chat["provider"], {}).get("name", chat["provider"])
        if status == 401:
            detail = f"API key {name} không hợp lệ hoặc đã bị vô hiệu hóa. Hãy cập nhật key mới trong Cài đặt API."
        elif status == 402:
            detail = f"Tài khoản {name} không đủ quota/số dư để gọi model này."
        elif status == 403:
            detail = f"API key {name} không có quyền sử dụng model {chat['model']}."
        elif status == 404:
            detail = f"Model {chat['model']} không tồn tại hoặc không còn khả dụng trên {name}."
        elif status == 429:
            detail = f"{name} đang giới hạn tần suất. Hãy thử lại sau."
        else:
            try:
                payload = exc.response.json()
                raw = payload.get("error", {}).get("message") or payload.get("message") or exc.response.text
            except Exception:
                raw = exc.response.text
            detail = f"{name} trả HTTP {status}: {str(raw)[:500]}"
        raise HTTPException(status, detail) from exc
    except Exception as exc:
        raise HTTPException(502, f"Không thể gọi model: {exc}") from exc

    # Chỉ ghi lịch sử khi provider trả lời thành công, tránh tạo tin nhắn trùng khi retry.
    add_message(chat_id, "user", text)
    add_message(chat_id, "assistant", answer)
    return get_chat(chat_id)
