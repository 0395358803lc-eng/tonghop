from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from .db import connect

SCENE_STATUSES = (
    "LOCKED",
    "WAITING_REFERENCE",
    "QUEUED",
    "GENERATING",
    "QC_RUNNING",
    "QC_FAILED",
    "REGENERATING",
    "APPROVED",
    "BLOCKED",
    "STALE",
)

RUN_STATUSES = ("idle", "running", "paused", "stopping", "stopped", "completed", "failed")

TRANSITIONS = {
    "LOCKED": {"WAITING_REFERENCE", "QUEUED", "BLOCKED", "STALE"},
    "WAITING_REFERENCE": {"QUEUED", "BLOCKED", "STALE", "LOCKED"},
    "QUEUED": {"GENERATING", "WAITING_REFERENCE", "BLOCKED", "STALE"},
    "GENERATING": {"QC_RUNNING", "QC_FAILED", "BLOCKED", "STALE"},
    "QC_RUNNING": {"APPROVED", "QC_FAILED", "BLOCKED"},
    "QC_FAILED": {"REGENERATING", "BLOCKED", "STALE", "APPROVED"},
    "REGENERATING": {"GENERATING", "QC_RUNNING", "BLOCKED", "STALE"},
    "APPROVED": {"STALE", "REGENERATING"},
    "BLOCKED": {"WAITING_REFERENCE", "QUEUED", "STALE", "LOCKED"},
    "STALE": {"WAITING_REFERENCE", "QUEUED", "BLOCKED", "LOCKED"},
}

DEFAULT_MAX_RETRIES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _row(row):
    if not row:
        return None
    data = dict(row)
    for key in (
        "qc_json",
        "repair_json",
        "snapshot_json",
        "best_rank_json",
        "gate_json",
        "log_json",
        "character_positions_json",
        "character_visibility_json",
        "prop_owner_json",
        "prop_holder_json",
        "prop_location_json",
        "prop_state_json",
    ):
        if key in data:
            dest = key[:-5] if key.endswith("_json") else key
            data[dest] = _loads(data.pop(key), {} if not key.endswith("repair_json") and not key.endswith("log_json") else [])
    if "paused" in data:
        data["paused"] = bool(data["paused"])
    for flag in ("stop_after_current", "hard_gates_passed", "is_best"):
        if flag in data:
            data[flag] = bool(data[flag])
    return data


def assert_status(status: str) -> str:
    value = str(status or "").strip().upper()
    if value not in SCENE_STATUSES:
        raise ValueError(f"SCENE_STATUS_INVALID: {status}")
    return value

def can_transition(current: str, nxt: str) -> bool:
    return assert_status(nxt) in TRANSITIONS.get(assert_status(current), set())


def transition_status(current: str, nxt: str) -> str:
    target = assert_status(nxt)
    source = assert_status(current)
    if target == source:
        return target
    if not can_transition(source, target):
        raise ValueError(f"SCENE_STATUS_TRANSITION_DENIED: {source} -> {target}")
    return target


def get_scene_state(project_id: str, scene_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_scene_pipeline WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        ).fetchone()
    return _row(row)


def list_scene_states(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_scene_pipeline WHERE project_id=? ORDER BY scene_index",
            (project_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def upsert_scene_state(project_id: str, scene_id: str, scene_index: int, **values) -> dict:
    current = get_scene_state(project_id, scene_id)
    force = bool(values.pop("force", False))
    incoming_status = values.get("status")
    if incoming_status and force:
        values["status"] = assert_status(incoming_status)
    elif incoming_status:
        source = (current or {}).get("status") or "LOCKED"
        values["status"] = transition_status(source, incoming_status)
    payload = {
        "scene_index": scene_index,
        "status": (current or {}).get("status") or "LOCKED",
        "attempt": (current or {}).get("attempt") or 0,
        "max_retries": (current or {}).get("max_retries") or DEFAULT_MAX_RETRIES,
        "current_run_id": (current or {}).get("current_run_id"),
        "current_job_id": (current or {}).get("current_job_id"),
        "selected_media_id": (current or {}).get("selected_media_id"),
        "best_media_id": (current or {}).get("best_media_id"),
        "best_score": (current or {}).get("best_score"),
        "best_rank": (current or {}).get("best_rank") or {},
        "error": (current or {}).get("error"),
        "blocked_reason": (current or {}).get("blocked_reason"),
        "qc": (current or {}).get("qc") or {},
        "repair": (current or {}).get("repair") or [],
        "snapshot": (current or {}).get("snapshot") or {},
    }
    payload.update(values)
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_scene_pipeline(
              project_id,scene_id,scene_index,status,attempt,max_retries,current_run_id,current_job_id,
              selected_media_id,best_media_id,best_score,best_rank_json,error,blocked_reason,qc_json,repair_json,snapshot_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,scene_id) DO UPDATE SET
              scene_index=excluded.scene_index,status=excluded.status,attempt=excluded.attempt,
              max_retries=excluded.max_retries,current_run_id=excluded.current_run_id,current_job_id=excluded.current_job_id,
              selected_media_id=excluded.selected_media_id,best_media_id=excluded.best_media_id,best_score=excluded.best_score,
              best_rank_json=excluded.best_rank_json,error=excluded.error,blocked_reason=excluded.blocked_reason,
              qc_json=excluded.qc_json,repair_json=excluded.repair_json,snapshot_json=excluded.snapshot_json,
              updated_at=excluded.updated_at""",
            (
                project_id,
                scene_id,
                int(payload["scene_index"]),
                payload["status"],
                int(payload["attempt"] or 0),
                int(payload["max_retries"] or DEFAULT_MAX_RETRIES),
                payload.get("current_run_id"),
                payload.get("current_job_id"),
                payload.get("selected_media_id"),
                payload.get("best_media_id"),
                payload.get("best_score"),
                json.dumps(payload.get("best_rank") or {}, ensure_ascii=False),
                payload.get("error"),
                payload.get("blocked_reason"),
                json.dumps(payload.get("qc") or {}, ensure_ascii=False),
                json.dumps(payload.get("repair") or [], ensure_ascii=False),
                json.dumps(payload.get("snapshot") or {}, ensure_ascii=False),
                _now(),
            ),
        )
    row = get_scene_state(project_id, scene_id)
    try:
        new_status = (row or {}).get("status")
        old_status = (current or {}).get("status")
        if incoming_status and new_status and new_status != old_status:
            from .film_observability_service import emit_scene_status_event
            emit_scene_status_event(
                project_id, scene_id, new_status,
                run_id=(row or {}).get("current_run_id"),
                job_id=(row or {}).get("current_job_id"),
                payload={"attempt": (row or {}).get("attempt"), "error": (row or {}).get("error")},
            )
    except Exception:
        pass
    return row


def ensure_scene_states(project_id: str, scenes: list[dict], max_retries: int = DEFAULT_MAX_RETRIES) -> list[dict]:
    for scene in scenes:
        existing = get_scene_state(project_id, scene["id"])
        if existing:
            continue
        upsert_scene_state(
            project_id,
            scene["id"],
            int(scene.get("scene_index") or 0),
            status="LOCKED",
            max_retries=max_retries,
        )
    return list_scene_states(project_id)


def get_ledger(project_id: str, scene_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_scene_ledger WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        ).fetchone()
    return _row(row)


def list_ledgers(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_scene_ledger WHERE project_id=? ORDER BY scene_id",
            (project_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def _as_text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def save_ledger(project_id: str, scene_id: str, payload: dict) -> dict:
    fields = {
        "selected_media_id": payload.get("selected_media_id"),
        "accepted_first_frame": payload.get("accepted_first_frame"),
        "accepted_last_frame": payload.get("accepted_last_frame"),
        "character_positions": payload.get("character_positions") or {},
        "character_pose": _as_text(payload.get("character_pose")),
        "character_wardrobe": _as_text(payload.get("character_wardrobe")),
        "character_visibility": payload.get("character_visibility") or {},
        "prop_owner": payload.get("prop_owner") or {},
        "prop_holder": payload.get("prop_holder") or {},
        "prop_location": payload.get("prop_location") or {},
        "prop_state": payload.get("prop_state") or {},
        "location_id": payload.get("location_id"),
        "time_of_day": _as_text(payload.get("time_of_day")),
        "lighting_state": _as_text(payload.get("lighting_state")),
        "camera_direction": _as_text(payload.get("camera_direction")),
        "dialogue_state": _as_text(payload.get("dialogue_state")),
        "audio_state": _as_text(payload.get("audio_state")),
        "snapshot": payload.get("snapshot") or payload,
    }
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_scene_ledger(
              project_id,scene_id,selected_media_id,accepted_first_frame,accepted_last_frame,
              character_positions_json,character_pose,character_wardrobe,character_visibility_json,
              prop_owner_json,prop_holder_json,prop_location_json,prop_state_json,location_id,
              time_of_day,lighting_state,camera_direction,dialogue_state,audio_state,snapshot_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,scene_id) DO UPDATE SET
              selected_media_id=excluded.selected_media_id,accepted_first_frame=excluded.accepted_first_frame,
              accepted_last_frame=excluded.accepted_last_frame,character_positions_json=excluded.character_positions_json,
              character_pose=excluded.character_pose,character_wardrobe=excluded.character_wardrobe,
              character_visibility_json=excluded.character_visibility_json,prop_owner_json=excluded.prop_owner_json,
              prop_holder_json=excluded.prop_holder_json,prop_location_json=excluded.prop_location_json,
              prop_state_json=excluded.prop_state_json,location_id=excluded.location_id,time_of_day=excluded.time_of_day,
              lighting_state=excluded.lighting_state,camera_direction=excluded.camera_direction,
              dialogue_state=excluded.dialogue_state,audio_state=excluded.audio_state,snapshot_json=excluded.snapshot_json,
              updated_at=excluded.updated_at""",
            (
                project_id,
                scene_id,
                fields["selected_media_id"],
                fields["accepted_first_frame"],
                fields["accepted_last_frame"],
                json.dumps(fields["character_positions"], ensure_ascii=False),
                fields["character_pose"],
                fields["character_wardrobe"],
                json.dumps(fields["character_visibility"], ensure_ascii=False),
                json.dumps(fields["prop_owner"], ensure_ascii=False),
                json.dumps(fields["prop_holder"], ensure_ascii=False),
                json.dumps(fields["prop_location"], ensure_ascii=False),
                json.dumps(fields["prop_state"], ensure_ascii=False),
                fields["location_id"],
                fields["time_of_day"],
                fields["lighting_state"],
                fields["camera_direction"],
                fields["dialogue_state"],
                fields["audio_state"],
                json.dumps(fields["snapshot"], ensure_ascii=False),
                _now(),
            ),
        )
    return get_ledger(project_id, scene_id)


def create_run(project_id: str, *, from_scene_id: str | None = None, scene_limit: int | None = None, gate: dict | None = None) -> dict:
    run_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_pipeline_runs(
              id,project_id,status,from_scene_id,scene_limit,stop_after_current,gate_json,log_json,started_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                project_id,
                "running",
                from_scene_id,
                scene_limit,
                0,
                json.dumps(gate or {}, ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                _now(),
                _now(),
            ),
        )
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_pipeline_runs WHERE id=?", (run_id,)).fetchone()
    return _row(row)


def get_active_run(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM film_pipeline_runs
               WHERE project_id=? AND status IN ('running','paused','stopping')
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
    return _row(row)


def latest_run(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_pipeline_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return _row(row)


def list_active_runs() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM film_pipeline_runs
               WHERE status IN ('running','paused','stopping')
               ORDER BY created_at ASC"""
        ).fetchall()
    return [_row(row) for row in rows if row]


def update_run(run_id: str, **values) -> dict | None:
    allowed = {
        "status",
        "current_scene_id",
        "current_scene_index",
        "stop_after_current",
        "from_scene_id",
        "scene_limit",
        "gate_json",
        "log_json",
        "error",
    }
    patch = {k: v for k, v in values.items() if k in allowed or k in {"gate", "log"}}
    if "gate" in patch:
        patch["gate_json"] = json.dumps(patch.pop("gate") or {}, ensure_ascii=False)
    if "log" in patch:
        patch["log_json"] = json.dumps(patch.pop("log") or [], ensure_ascii=False)
    if "stop_after_current" in patch:
        patch["stop_after_current"] = 1 if patch["stop_after_current"] else 0
    if patch:
        parts = [f"{key}=?" for key in patch]
        with connect() as conn:
            conn.execute(
                f"UPDATE film_pipeline_runs SET {', '.join(parts)},updated_at=? WHERE id=?",
                list(patch.values()) + [_now(), run_id],
            )
    return get_run(run_id)


def append_run_log(run_id: str, entry: dict) -> dict | None:
    run = get_run(run_id)
    if not run:
        return None
    log = list(run.get("log") or [])
    item = dict(entry)
    item.setdefault("at", _now())
    log.append(item)
    updated = update_run(run_id, log=log[-200:])
    try:
        from .film_observability_service import emit_run_log_event
        emit_run_log_event(run.get("project_id"), run_id, item)
    except Exception:
        pass
    return updated


def record_candidate(
    project_id: str,
    scene_id: str,
    *,
    run_id: str | None,
    job_id: str | None,
    media_id: str | None,
    attempt: int,
    hard_gates_passed: bool,
    dimensions_passed: int,
    overall_score: float | None,
    qc: dict | None,
) -> dict:
    candidate_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_pipeline_candidates(
              id,project_id,scene_id,run_id,job_id,media_id,attempt,hard_gates_passed,
              dimensions_passed,overall_score,qc_json,is_best,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            (
                candidate_id,
                project_id,
                scene_id,
                run_id,
                job_id,
                media_id,
                int(attempt or 0),
                1 if hard_gates_passed else 0,
                int(dimensions_passed or 0),
                overall_score,
                json.dumps(qc or {}, ensure_ascii=False),
                _now(),
            ),
        )
    best = select_best_candidate(project_id, scene_id)
    return get_candidate(candidate_id) or best


def get_candidate(candidate_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_pipeline_candidates WHERE id=?", (candidate_id,)).fetchone()
    return _row(row)


def list_candidates(project_id: str, scene_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM film_pipeline_candidates
               WHERE project_id=? AND scene_id=?
               ORDER BY hard_gates_passed DESC, dimensions_passed DESC, overall_score DESC, created_at DESC""",
            (project_id, scene_id),
        ).fetchall()
    return [_row(row) for row in rows]


def candidate_rank(item: dict) -> tuple:
    return (
        1 if item.get("hard_gates_passed") else 0,
        int(item.get("dimensions_passed") or 0),
        float(item.get("overall_score") or 0),
    )


def select_best_candidate(project_id: str, scene_id: str) -> dict | None:
    items = list_candidates(project_id, scene_id)
    if not items:
        return None
    winner = max(items, key=candidate_rank)
    with connect() as conn:
        conn.execute(
            "UPDATE film_pipeline_candidates SET is_best=0 WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        )
        conn.execute("UPDATE film_pipeline_candidates SET is_best=1 WHERE id=?", (winner["id"],))
    return get_candidate(winner["id"])


def has_previous_scene(project_id: str, scene_index: int) -> bool:
    prev = int(scene_index) - 1
    with connect() as conn:
        row = conn.execute(
            "SELECT scene_id FROM film_scene_pipeline WHERE project_id=? AND scene_index=? LIMIT 1",
            (project_id, prev),
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT id FROM film_scenes WHERE project_id=? AND scene_index=? LIMIT 1",
            (project_id, prev),
        ).fetchone()
        return bool(row)


def previous_approved_scene(project_id: str, scene_index: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM film_scene_pipeline
               WHERE project_id=? AND scene_index=? AND status='APPROVED'""",
            (project_id, scene_index - 1),
        ).fetchone()
    return _row(row)


LEASE_TTL_SECONDS = 120


def get_execution_lease(project_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_pipeline_execution_leases WHERE project_id=?",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def execution_lease_active(project_id: str) -> bool:
    lease = get_execution_lease(project_id)
    if not lease:
        return False
    try:
        until = datetime.fromisoformat(str(lease.get("lease_until") or ""))
    except Exception:
        return False
    now = datetime.now(timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now


def acquire_execution_lease(project_id: str, *, worker_id: str, run_id: str | None = None, scene_id: str | None = None, ttl: int = LEASE_TTL_SECONDS) -> dict:
    current = get_execution_lease(project_id)
    if current and execution_lease_active(project_id) and current.get("worker_id") != worker_id:
        return current
    until = (datetime.now(timezone.utc) + timedelta(seconds=max(15, int(ttl)))).isoformat()
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_pipeline_execution_leases(project_id,scene_id,run_id,worker_id,lease_until,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 scene_id=excluded.scene_id, run_id=excluded.run_id, worker_id=excluded.worker_id,
                 lease_until=excluded.lease_until, updated_at=excluded.updated_at""",
            (project_id, scene_id, run_id, worker_id, until, now),
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(film_pipeline_execution_leases)").fetchall()}
        if "heartbeat_at" in cols:
            conn.execute("UPDATE film_pipeline_execution_leases SET heartbeat_at=? WHERE project_id=?", (now, project_id))
    return get_execution_lease(project_id) or {}


def heartbeat_execution_lease(project_id: str, worker_id: str, *, scene_id: str | None = None, run_id: str | None = None, ttl: int = LEASE_TTL_SECONDS) -> dict | None:
    current = get_execution_lease(project_id)
    if not current or current.get("worker_id") != worker_id:
        return current
    return acquire_execution_lease(project_id, worker_id=worker_id, run_id=run_id or current.get("run_id"), scene_id=scene_id or current.get("scene_id"), ttl=ttl)


def release_execution_lease(project_id: str, worker_id: str | None = None) -> None:
    current = get_execution_lease(project_id)
    if worker_id and current and current.get("worker_id") != worker_id:
        return
    with connect() as conn:
        conn.execute("DELETE FROM film_pipeline_execution_leases WHERE project_id=?", (project_id,))
