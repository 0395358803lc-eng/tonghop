from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from .film_event_store import emit_event, list_recent_events
from .film_resource_store import list_project_resources, update_resource_binding

ACTIVE_RUN_STATES = {"queued", "running", "stopping"}
ACTIVE_RESOURCE_STATES = {
    "queued", "starting", "opening_flow_project", "awaiting_generation",
    "downloading_result", "downloaded", "qc_running", "regenerating", "stopping",
}
_LOCK = threading.Lock()
_RUNS: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(project_id: str) -> dict | None:
    with _LOCK:
        run = _RUNS.get(project_id)
        return dict(run) if run else None
def begin_run(project_id: str, selected: list[dict], provider: str, model: str) -> dict | None:
    with _LOCK:
        current = _RUNS.get(project_id)
        if current and current.get("status") in ACTIVE_RUN_STATES:
            return None
        run = {
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "status": "queued",
            "provider": provider,
            "model": model,
            "requested": len(selected),
            "completed": 0,
            "failed": 0,
            "current_resource_type": None,
            "current_entity_id": None,
            "stop_requested": False,
            "error": None,
            "created_at": _now(),
            "started_at": None,
            "updated_at": _now(),
            "finished_at": None,
        }
        _RUNS[project_id] = run
    emit_event(project_id, "CANONICAL_RUN_QUEUED", run_id=run["id"], payload={
        "message": "Đã xếp hàng tạo ảnh chuẩn.", "provider": provider, "model": model,
        "requested": len(selected),
    })
    return dict(run)
def mark_run_started(project_id: str, run_id: str) -> dict | None:
    with _LOCK:
        run = _RUNS.get(project_id)
        if not run or run.get("id") != run_id:
            return None
        run.update({"status": "running", "started_at": run.get("started_at") or _now(), "updated_at": _now()})
        result = dict(run)
    emit_event(project_id, "CANONICAL_RUN_STARTED", run_id=run_id, payload={"message": "Bắt đầu tạo bộ ảnh chuẩn."})
    return result


def set_current_resource(project_id: str, run_id: str, resource_type: str | None, entity_id: str | None) -> None:
    with _LOCK:
        run = _RUNS.get(project_id)
        if not run or run.get("id") != run_id:
            return
        run["current_resource_type"] = resource_type
        run["current_entity_id"] = entity_id
        run["updated_at"] = _now()


def stop_requested(project_id: str, run_id: str | None = None) -> bool:
    with _LOCK:
        run = _RUNS.get(project_id)
        return bool(run and (run_id is None or run.get("id") == run_id) and run.get("stop_requested"))
def request_stop(project_id: str) -> dict:
    with _LOCK:
        run = _RUNS.get(project_id)
        if not run or run.get("status") not in ACTIVE_RUN_STATES:
            return {"requested": False, "reason": "no_active_run", "run": dict(run) if run else None}
        run["stop_requested"] = True
        run["status"] = "stopping"
        run["updated_at"] = _now()
        result = dict(run)
    emit_event(project_id, "CANONICAL_RUN_STOP_REQUESTED", run_id=result["id"], severity="WARN",
               payload={"message": "Người dùng yêu cầu dừng tạo ảnh chuẩn."})
    return {"requested": True, "run": result}


def finish_run(project_id: str, run_id: str, status: str, *, completed: int = 0, failed: int = 0, error: str | None = None) -> dict | None:
    with _LOCK:
        run = _RUNS.get(project_id)
        if not run or run.get("id") != run_id:
            return None
        run.update({
            "status": status, "completed": completed, "failed": failed, "error": error,
            "current_resource_type": None, "current_entity_id": None, "updated_at": _now(), "finished_at": _now(),
        })
        result = dict(run)
    event = "CANONICAL_RUN_STOPPED" if status == "stopped" else "CANONICAL_RUN_COMPLETED" if status == "completed" else "CANONICAL_RUN_FAILED"
    severity = "INFO" if status in {"completed", "stopped"} else "ERROR"
    emit_event(project_id, event, run_id=run_id, severity=severity,
               payload={"message": f"Kết thúc tạo ảnh chuẩn: {status}.", "completed": completed, "failed": failed, "error": error})
    return result
def bump_result(project_id: str, run_id: str, *, completed: int = 0, failed: int = 0) -> None:
    with _LOCK:
        run = _RUNS.get(project_id)
        if not run or run.get("id") != run_id:
            return
        run["completed"] = int(run.get("completed") or 0) + completed
        run["failed"] = int(run.get("failed") or 0) + failed
        run["updated_at"] = _now()


def mark_resource(project_id: str, resource_type: str, entity_id: str, generation_status: str, *,
                  run_id: str | None = None, status: str | None = None, error: str | None = None,
                  metadata_patch: dict | None = None) -> dict:
    patch = dict(metadata_patch or {})
    patch.update({"generation_status": generation_status, "canonical_run_id": run_id})
    return update_resource_binding(
        project_id, resource_type, entity_id, provider="flow", status=status, error=error, metadata_patch=patch,
    )


def emit_resource_event(project_id: str, run_id: str | None, event_type: str, resource_type: str, entity_id: str,
                        *, severity: str = "INFO", message: str = "", payload: dict | None = None) -> None:
    body = {"resource_type": resource_type, "entity_id": entity_id, "message": message}
    body.update(payload or {})
    emit_event(project_id, event_type, run_id=run_id, severity=severity, payload=body)
def reconcile_orphaned_resources(project_id: str) -> list[dict]:
    run = _snapshot(project_id)
    if run and run.get("status") in ACTIVE_RUN_STATES:
        return list_project_resources(project_id, "flow")
    resources = list_project_resources(project_id, "flow")
    for item in resources:
        metadata = item.get("metadata") or {}
        generation_status = str(metadata.get("generation_status") or "")
        if generation_status not in ACTIVE_RESOURCE_STATES:
            continue
        has_file = bool(item.get("local_path"))
        next_status = item.get("status") if has_file else "error"
        next_error = item.get("error") if has_file else "CANONICAL_RUN_INTERRUPTED: Tiến trình tạo ảnh trước đã kết thúc ngoài dự kiến."
        mark_resource(
            project_id, str(item["resource_type"]), str(item["entity_id"]), "failed",
            run_id=None, status=next_status, error=next_error,
            metadata_patch={"interrupted_reconciled_at": _now()},
        )
        emit_resource_event(project_id, None, "CANONICAL_RESOURCE_FAILED", str(item["resource_type"]), str(item["entity_id"]),
                            severity="WARN", message="Khôi phục trạng thái run bị gián đoạn.")
    return list_project_resources(project_id, "flow")
def canonical_status(project_id: str, *, reconcile: bool = True) -> dict:
    resources = reconcile_orphaned_resources(project_id) if reconcile else list_project_resources(project_id, "flow")
    counts = {"total": 0, "completed": 0, "failed": 0, "stopped": 0, "running": 0, "with_file": 0, "qc_failed": 0}
    by_type: dict[str, dict] = {}
    for item in resources:
        if item.get("status") == "retired":
            continue
        counts["total"] += 1
        typ = str(item.get("resource_type") or "unknown")
        bucket = by_type.setdefault(typ, {"total": 0, "completed": 0, "failed": 0, "running": 0, "with_file": 0})
        bucket["total"] += 1
        meta = item.get("metadata") or {}
        gen = str(meta.get("generation_status") or "")
        qc = meta.get("canonical_qc") if isinstance(meta.get("canonical_qc"), dict) else {}
        passed = bool((qc.get("hard_gate") or {}).get("passed") is True)
        if item.get("local_path"):
            counts["with_file"] += 1; bucket["with_file"] += 1
        if item.get("status") in {"ready", "locked"} and passed:
            counts["completed"] += 1; bucket["completed"] += 1
        if item.get("status") == "error":
            counts["failed"] += 1; bucket["failed"] += 1
        if gen == "stopped":
            counts["stopped"] += 1
        if gen in ACTIVE_RESOURCE_STATES:
            counts["running"] += 1; bucket["running"] += 1
        if str(meta.get("canonical_qc_status") or "") == "failed":
            counts["qc_failed"] += 1
    run = _snapshot(project_id)
    events = list_recent_events(project_id, event_prefix="CANONICAL_", limit=100)
    return {"project_id": project_id, "run": run, "active": bool(run and run.get("status") in ACTIVE_RUN_STATES),
            "counts": counts, "by_type": by_type, "resources": resources, "events": events}
