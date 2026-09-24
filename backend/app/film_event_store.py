from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from .db import connect

logger = logging.getLogger(__name__)

_EMIT_FAILURES = 0

EVENT_TYPES = (
    "PIPELINE_RUN_STARTED",
    "PIPELINE_RUN_PAUSED",
    "PIPELINE_RUN_RESUMED",
    "PIPELINE_RUN_STOP_REQUESTED",
    "PIPELINE_RUN_COMPLETED",
    "PIPELINE_RUN_FAILED",
    "SCENE_WAITING",
    "SCENE_QUEUED",
    "SCENE_GENERATING",
    "SCENE_QC_RUNNING",
    "SCENE_QC_FAILED",
    "SCENE_REGENERATING",
    "SCENE_APPROVED",
    "SCENE_BLOCKED",
    "SCENE_STALE",
    "FLOW_JOB_CREATED",
    "FLOW_JOB_STARTED",
    "FLOW_JOB_COMPLETED",
    "FLOW_JOB_FAILED",
    "FLOW_JOB_TIMEOUT",
    "VIDEO_QC_STARTED",
    "VIDEO_QC_PASSED",
    "VIDEO_QC_FAILED",
    "JUNCTION_QC_STARTED",
    "JUNCTION_QC_PASSED",
    "JUNCTION_QC_FAILED",
    "JUNCTION_REPAIR_STARTED",
    "JUNCTION_REPAIR_COMPLETED",
    "FINAL_ASSEMBLY_STARTED",
    "FINAL_ASSEMBLY_COMPLETED",
    "MASTER_QC_STARTED",
    "MASTER_QC_PASSED",
    "MASTER_QC_FAILED",
    "SNAPSHOT_CREATED",
    "SNAPSHOT_BACKFILLED",
    "SNAPSHOT_STALE",
    "LEASE_RECLAIMED",
    "ORPHAN_JOB_DETECTED",
    "RECOVERY_RECONCILED",
    "CAPABILITY_REFRESHED",
    "CAPABILITY_BLOCKED",
)

SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR")
SCENE_STATUS_EVENTS = {
    "WAITING_REFERENCE": "SCENE_WAITING",
    "QUEUED": "SCENE_QUEUED",
    "GENERATING": "SCENE_GENERATING",
    "QC_RUNNING": "SCENE_QC_RUNNING",
    "QC_FAILED": "SCENE_QC_FAILED",
    "REGENERATING": "SCENE_REGENERATING",
    "APPROVED": "SCENE_APPROVED",
    "BLOCKED": "SCENE_BLOCKED",
    "STALE": "SCENE_STALE",
}
RUN_LOG_EVENTS = {
    "start": "PIPELINE_RUN_STARTED",
    "pause": "PIPELINE_RUN_PAUSED",
    "resume": "PIPELINE_RUN_RESUMED",
    "stop_after_current": "PIPELINE_RUN_STOP_REQUESTED",
    "stop": "PIPELINE_RUN_STOP_REQUESTED",
    "completed": "PIPELINE_RUN_COMPLETED",
    "scene_failed": "PIPELINE_RUN_FAILED",
    "generate": "FLOW_JOB_STARTED",
    "qc_failed": "VIDEO_QC_FAILED",
    "retry_scene": "SCENE_REGENERATING",
    "approved": "SCENE_APPROVED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    raw = data.pop("payload_json", None)
    try:
        data["payload"] = json.loads(raw) if raw else {}
    except Exception:
        data["payload"] = {}
    return data


def emit_event(
    project_id: str,
    event_type: str,
    *,
    run_id: str | None = None,
    scene_id: str | None = None,
    job_id: str | None = None,
    severity: str = "INFO",
    payload: dict | None = None,
) -> dict | None:
    if not project_id or event_type not in EVENT_TYPES:
        return None
    event_id = str(uuid.uuid4())
    body = dict(payload or {})
    body.setdefault("project_id", project_id)
    if run_id:
        body.setdefault("run_id", run_id)
    if scene_id:
        body.setdefault("scene_id", scene_id)
    if job_id:
        body.setdefault("job_id", job_id)
    sev = severity if severity in SEVERITIES else "INFO"
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO film_pipeline_events(
                     id,project_id,run_id,scene_id,job_id,event_type,severity,payload_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    project_id,
                    run_id,
                    scene_id,
                    job_id,
                    event_type,
                    sev,
                    json.dumps(body, ensure_ascii=False),
                    _now(),
                ),
            )
        return get_event(event_id)
    except Exception:
        # Events are the acceptance evidence trail; a silent failure here means a gate
        # that reports nothing happened rather than a gate that reports a problem.
        global _EMIT_FAILURES
        _EMIT_FAILURES += 1
        logger.warning("event_emit_failed project=%s type=%s", project_id, event_type, exc_info=True)
        return None


def emit_failure_count() -> int:
    return _EMIT_FAILURES


def get_event(event_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_pipeline_events WHERE id=?", (event_id,)).fetchone()
    return _row(row)


def list_events(
    project_id: str,
    *,
    run_id: str | None = None,
    scene_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    clauses = ["project_id=?"]
    args: list = [project_id]
    if run_id:
        clauses.append("run_id=?")
        args.append(run_id)
    if scene_id:
        clauses.append("scene_id=?")
        args.append(scene_id)
    if event_type:
        clauses.append("event_type=?")
        args.append(event_type)
    if severity:
        clauses.append("severity=?")
        args.append(severity)
    if from_ts:
        clauses.append("created_at>=?")
        args.append(from_ts)
    if to_ts:
        clauses.append("created_at<=?")
        args.append(to_ts)
    sql = (
        "SELECT * FROM film_pipeline_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?"
    )
    args.extend([max(1, min(int(limit or 200), 500)), max(0, int(offset or 0))])
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row(row) for row in rows]


def event_store_available() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1 FROM film_pipeline_events LIMIT 1")
        return True
    except Exception:
        return False


SYSTEM_SCOPE = "system"


def purge_project_events(conn, project_id: str) -> int:
    """Delete a project's events inside the caller's transaction and return how many went.

    film_pipeline_events has no foreign key, so deleting a project left its log rows behind
    permanently - the source of ~20k orphan rows in the acceptance store. Rows under
    SYSTEM_SCOPE are app-wide diagnostics with no owning project, so they are never in scope.
    """
    if not project_id or project_id == SYSTEM_SCOPE:
        return 0
    return int(
        conn.execute("DELETE FROM film_pipeline_events WHERE project_id=?", (project_id,)).rowcount or 0
    )


def count_events_without_project() -> int:
    """Orphan count, i.e. the invariant every project delete must preserve."""
    with connect() as conn:
        return int(
            conn.execute(
                """SELECT COUNT(*) FROM film_pipeline_events
                   WHERE project_id NOT IN (SELECT id FROM film_projects)
                     AND project_id<>?"""
            , (SYSTEM_SCOPE,)).fetchone()[0]
        )
