from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from .db import connect
from .film_event_store import emit_event
from .film_render_store import ACTIVE_RENDER_STATUSES, get_render_job
from .film_scene_state_store import (
    acquire_execution_lease,
    execution_lease_active,
    get_active_run,
    get_execution_lease,
    get_scene_state,
    list_scene_states,
    release_execution_lease,
    upsert_scene_state,
)

ORPHAN_AFTER = timedelta(minutes=3)
IDEMPOTENCY_TTL = timedelta(seconds=20)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def reclaim_expired_lease(project_id: str) -> dict:
    lease = get_execution_lease(project_id)
    if not lease:
        return {"reclaimed": False, "reason": "missing"}
    if execution_lease_active(project_id):
        return {"reclaimed": False, "reason": "active", "lease": lease}
    release_execution_lease(project_id)
    emit_event(project_id, "LEASE_RECLAIMED", run_id=lease.get("run_id"), scene_id=lease.get("scene_id"), payload={"worker_id": lease.get("worker_id")})
    return {"reclaimed": True, "lease": lease}


def classify_render_job(job: dict | None, lease_active: bool) -> str:
    if not job:
        return "missing"
    status = str(job.get("status") or "")
    if status == "completed" and job.get("result_url"):
        return "completed_externally"
    if status == "failed":
        return "failed_externally"
    if status in ACTIVE_RENDER_STATUSES:
        if job.get("provider_job_id") and lease_active:
            return "still_active"
        updated = _parse(job.get("updated_at"))
        stale = (not updated) or (_now() - updated > ORPHAN_AFTER)
        if not job.get("provider_job_id") and not lease_active and stale:
            return "orphan"
        if not lease_active and stale:
            return "stale"
        return "still_active"
    return status or "unknown"


def detect_orphan_jobs(project_id: str) -> list[dict]:
    lease_active = execution_lease_active(project_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_render_jobs WHERE project_id=? AND status IN ('waiting','preparing','generating') ORDER BY created_at",
            (project_id,),
        ).fetchall()
    orphans = []
    for row in rows:
        job = dict(row)
        kind = classify_render_job(job, lease_active)
        if kind == "orphan":
            emit_event(project_id, "ORPHAN_JOB_DETECTED", scene_id=job.get("scene_id"), job_id=job.get("id"), severity="WARN", payload={"provider_job_id": job.get("provider_job_id")})
            orphans.append(job)
    return orphans


def _recover_completed_job(project_id: str, scene: dict, job: dict) -> str:
    qc_status = str(job.get("qc_status") or "not_run")
    if qc_status == "passed":
        return "already_qc_passed"
    upsert_scene_state(
        project_id,
        scene["scene_id"],
        int(scene.get("scene_index") or 0),
        status="QC_RUNNING",
        current_job_id=job.get("id"),
        error=None,
        blocked_reason=None,
        force=True,
    )
    return "continue_qc"


def reconcile_project(project_id: str) -> dict:
    lease = reclaim_expired_lease(project_id)
    run = get_active_run(project_id)
    scenes = list_scene_states(project_id)
    orphans = detect_orphan_jobs(project_id)
    recovered = []
    stuck = []
    for scene in scenes:
        status = scene.get("status")
        job = get_render_job(str(scene.get("current_job_id") or "")) if scene.get("current_job_id") else None
        kind = classify_render_job(job, execution_lease_active(project_id))
        if status in {"GENERATING", "QC_RUNNING", "REGENERATING"} and kind == "completed_externally":
            action = _recover_completed_job(project_id, scene, job)
            recovered.append({"scene_id": scene["scene_id"], "job_id": job.get("id"), "action": action})
        elif status == "GENERATING" and kind == "orphan":
            stuck.append({"scene_id": scene["scene_id"], "job_id": (job or {}).get("id"), "action": "orphan"})
        elif status in {"GENERATING", "QC_RUNNING"} and kind == "failed_externally":
            upsert_scene_state(
                project_id, scene["scene_id"], int(scene.get("scene_index") or 0),
                status="QC_FAILED", error=(job or {}).get("error") or "provider failed externally",
            )
            recovered.append({"scene_id": scene["scene_id"], "action": "mark_qc_failed"})
    if run and run.get("status") in {"running", "stopping"} and not execution_lease_active(project_id):
        # worker died; keep run so resume can reclaim. Do not duplicate.
        stuck.append({"run_id": run.get("id"), "action": "run_orphaned_lease"})
    report = {
        "project_id": project_id,
        "lease": lease,
        "run_id": (run or {}).get("id"),
        "run_status": (run or {}).get("status"),
        "recovered": recovered,
        "orphans": [{"id": item.get("id"), "scene_id": item.get("scene_id")} for item in orphans],
        "stuck": stuck,
        "duplicate_risk": False,
    }
    emit_event(project_id, "RECOVERY_RECONCILED", run_id=(run or {}).get("id"), payload=report)
    return report


def reconcile_all() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT project_id FROM film_scene_pipeline").fetchall()
        extra = conn.execute("SELECT DISTINCT project_id FROM film_pipeline_runs WHERE status IN ('running','paused','stopping')").fetchall()
    ids = {row["project_id"] for row in list(rows) + list(extra)}
    return [reconcile_project(pid) for pid in sorted(ids)]


def begin_idempotent(project_id: str, operation: str, fingerprint: str = "") -> dict:
    key = f"{project_id}:{operation}:{fingerprint or '*'}"
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_idempotency_keys WHERE key=?", (key,)).fetchone()
        if row:
            expires = _parse(row["expires_at"])
            if expires and expires > now:
                try:
                    result = json.loads(row["result_json"] or "{}")
                except Exception:
                    result = {}
                return {"hit": True, "key": key, "result": result}
        token = str(uuid.uuid4())
        expires_at = (now + IDEMPOTENCY_TTL).isoformat()
        conn.execute(
            """INSERT INTO film_idempotency_keys(key,project_id,operation,result_json,created_at,expires_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET created_at=excluded.created_at, expires_at=excluded.expires_at, result_json=excluded.result_json""",
            (key, project_id, operation, json.dumps({"pending": token}), now.isoformat(), expires_at),
        )
    return {"hit": False, "key": key, "token": token}


def finish_idempotent(key: str, result: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE film_idempotency_keys SET result_json=?, expires_at=? WHERE key=?",
            (json.dumps(result, ensure_ascii=False, default=str), (_now() + IDEMPOTENCY_TTL).isoformat(), key),
        )


def recovery_state_clean(project_id: str) -> bool:
    if detect_orphan_jobs(project_id):
        return False
    lease = get_execution_lease(project_id)
    if lease and not execution_lease_active(project_id):
        return False
    return True
