from __future__ import annotations

from datetime import datetime, timezone

from .film_event_store import RUN_LOG_EVENTS, SCENE_STATUS_EVENTS, emit_event, list_events


def _parse_ts(value: str | None):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except Exception:
        return None


def _duration_seconds(events: list[dict], start_type: str, end_types: tuple[str, ...]) -> float | None:
    start = next((item for item in events if item.get("event_type") == start_type), None)
    if not start:
        return None
    start_ts = _parse_ts(start.get("created_at"))
    end = None
    for item in events:
        if item.get("event_type") in end_types and _parse_ts(item.get("created_at")) and start_ts and _parse_ts(item.get("created_at")) >= start_ts:
            end = item
            break
    end_ts = _parse_ts((end or {}).get("created_at"))
    if not start_ts or not end_ts:
        return None
    return max(0.0, (end_ts - start_ts).total_seconds())


def emit_scene_status_event(project_id: str, scene_id: str, status: str, *, run_id: str | None = None, job_id: str | None = None, payload: dict | None = None) -> dict | None:
    event_type = SCENE_STATUS_EVENTS.get(str(status or "").upper())
    if not event_type:
        return None
    severity = "ERROR" if status in {"QC_FAILED", "BLOCKED"} else "WARN" if status == "STALE" else "INFO"
    body = dict(payload or {})
    body["status"] = status
    extra = {}
    if event_type == "SCENE_QC_RUNNING":
        emit_event(project_id, "VIDEO_QC_STARTED", run_id=run_id, scene_id=scene_id, job_id=job_id, payload=body)
    elif event_type == "SCENE_APPROVED":
        emit_event(project_id, "VIDEO_QC_PASSED", run_id=run_id, scene_id=scene_id, job_id=job_id, payload=body)
    elif event_type == "SCENE_QC_FAILED":
        emit_event(project_id, "VIDEO_QC_FAILED", run_id=run_id, scene_id=scene_id, job_id=job_id, severity="ERROR", payload=body)
    return emit_event(project_id, event_type, run_id=run_id, scene_id=scene_id, job_id=job_id, severity=severity, payload=body)


def emit_run_log_event(project_id: str, run_id: str, entry: dict) -> dict | None:
    action = str((entry or {}).get("action") or "")
    event_type = RUN_LOG_EVENTS.get(action)
    if not event_type:
        return None
    severity = "ERROR" if event_type.endswith("_FAILED") or event_type == "VIDEO_QC_FAILED" else "INFO"
    return emit_event(
        project_id,
        event_type,
        run_id=run_id,
        scene_id=(entry or {}).get("scene_id"),
        job_id=(entry or {}).get("job_id"),
        severity=severity,
        payload=entry,
    )


def emit_flow_job_event(job: dict, event_type: str, extra: dict | None = None) -> dict | None:
    if not job:
        return None
    payload = dict(extra or {})
    payload.update({
        "provider_job_id": job.get("provider_job_id"),
        "adapter": job.get("adapter"),
        "attempt": job.get("attempt"),
        "status": job.get("status"),
        "error": job.get("error"),
        "provider_error_code": job.get("provider_error_code"),
    })
    severity = "ERROR" if event_type in {"FLOW_JOB_FAILED", "FLOW_JOB_TIMEOUT"} else "INFO"
    return emit_event(
        job.get("project_id") or "",
        event_type,
        run_id=None,
        scene_id=job.get("scene_id"),
        job_id=job.get("id"),
        severity=severity,
        payload=payload,
    )


def project_metrics(project_id: str, run_id: str | None = None) -> dict:
    events = list_events(project_id, run_id=run_id, limit=500)
    by_type: dict[str, int] = {}
    for item in events:
        key = str(item.get("event_type") or "")
        by_type[key] = by_type.get(key, 0) + 1
    scene_groups: dict[str, list] = {}
    for item in events:
        sid = item.get("scene_id")
        if sid:
            scene_groups.setdefault(sid, []).append(item)
    scene_durations = []
    flow_waits = []
    qc_durations = []
    junction_durations = []
    for sid, group in scene_groups.items():
        dur = _duration_seconds(group, "SCENE_GENERATING", ("SCENE_APPROVED", "SCENE_BLOCKED", "SCENE_QC_FAILED"))
        if dur is not None:
            scene_durations.append(dur)
        wait = _duration_seconds(group, "FLOW_JOB_STARTED", ("FLOW_JOB_COMPLETED", "FLOW_JOB_FAILED", "FLOW_JOB_TIMEOUT"))
        if wait is not None:
            flow_waits.append(wait)
        qc = _duration_seconds(group, "VIDEO_QC_STARTED", ("VIDEO_QC_PASSED", "VIDEO_QC_FAILED"))
        if qc is not None:
            qc_durations.append(qc)
        junc = _duration_seconds(group, "JUNCTION_QC_STARTED", ("JUNCTION_QC_PASSED", "JUNCTION_QC_FAILED"))
        if junc is not None:
            junction_durations.append(junc)
    avg = (lambda values: round(sum(values) / len(values), 3) if values else None)
    return {
        "project_id": project_id,
        "run_id": run_id,
        "event_count": len(events),
        "by_type": by_type,
        "attempt_events": by_type.get("SCENE_REGENERATING", 0) + by_type.get("FLOW_JOB_CREATED", 0),
        "retry_count": by_type.get("SCENE_REGENERATING", 0),
        "success_count": by_type.get("SCENE_APPROVED", 0),
        "fail_count": by_type.get("SCENE_QC_FAILED", 0) + by_type.get("SCENE_BLOCKED", 0),
        "timeout_count": by_type.get("FLOW_JOB_TIMEOUT", 0),
        "scene_render_duration_avg_sec": avg(scene_durations),
        "flow_wait_duration_avg_sec": avg(flow_waits),
        "qc_duration_avg_sec": avg(qc_durations),
        "junction_qc_duration_avg_sec": avg(junction_durations),
        "final_assembly_duration_sec": _duration_seconds(events, "FINAL_ASSEMBLY_STARTED", ("FINAL_ASSEMBLY_COMPLETED",)),
    }
