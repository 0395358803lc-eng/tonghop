from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import connect

JUNCTION_STATUSES = ("PENDING", "RUNNING", "PASS", "FAIL", "REPAIRING", "BLOCKED", "STALE")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _row(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    data["qc"] = _loads(data.pop("qc_json", None), {})
    data["attempt"] = int(data.get("attempt") or 0)
    data["score"] = data.get("score")
    return data


def junction_id_for(previous_scene_id: str, next_scene_id: str, project_id: str | None = None) -> str:
    if project_id:
        return f"{project_id}::{previous_scene_id}__{next_scene_id}"
    return f"{previous_scene_id}__{next_scene_id}"


def list_junctions(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_scene_junctions WHERE project_id=? ORDER BY created_at, previous_scene_id",
            (project_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def get_junction(project_id: str, junction_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_scene_junctions WHERE project_id=? AND id=?",
            (project_id, junction_id),
        ).fetchone()
        if not row and "__" in junction_id:
            token = junction_id.split("::", 1)[-1]
            prev, nxt = (token.split("__", 1) + [""])[:2]
            row = conn.execute(
                "SELECT * FROM film_scene_junctions WHERE project_id=? AND previous_scene_id=? AND next_scene_id=?",
                (project_id, prev, nxt),
            ).fetchone()
    return _row(row)


def get_pair_junction(project_id: str, previous_scene_id: str, next_scene_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_scene_junctions WHERE project_id=? AND previous_scene_id=? AND next_scene_id=?",
            (project_id, previous_scene_id, next_scene_id),
        ).fetchone()
    return _row(row)


def upsert_junction(project_id: str, previous_scene_id: str, next_scene_id: str, **values) -> dict:
    current = get_pair_junction(project_id, previous_scene_id, next_scene_id)
    jid = (current or {}).get("id") or values.get("id") or junction_id_for(previous_scene_id, next_scene_id, project_id)
    status = values.get("status") or (current or {}).get("status") or "PENDING"
    if status not in JUNCTION_STATUSES:
        status = "PENDING"
    qc = values.get("qc") if "qc" in values else (current or {}).get("qc") or {}
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_scene_junctions(
                 id,project_id,previous_scene_id,next_scene_id,status,
                 selected_previous_media_id,selected_next_media_id,qc_json,score,attempt,error,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id, previous_scene_id, next_scene_id) DO UPDATE SET
                 status=excluded.status,
                 selected_previous_media_id=COALESCE(excluded.selected_previous_media_id, film_scene_junctions.selected_previous_media_id),
                 selected_next_media_id=COALESCE(excluded.selected_next_media_id, film_scene_junctions.selected_next_media_id),
                 qc_json=excluded.qc_json,
                 score=excluded.score,
                 attempt=excluded.attempt,
                 error=excluded.error,
                 updated_at=excluded.updated_at""",
            (
                jid, project_id, previous_scene_id, next_scene_id, status,
                values.get("selected_previous_media_id", (current or {}).get("selected_previous_media_id")),
                values.get("selected_next_media_id", (current or {}).get("selected_next_media_id")),
                json.dumps(qc, ensure_ascii=False),
                values.get("score", (current or {}).get("score")),
                int(values.get("attempt", (current or {}).get("attempt") or 0)),
                values.get("error", (current or {}).get("error")),
                (current or {}).get("created_at") or now,
                now,
            ),
        )
    return get_pair_junction(project_id, previous_scene_id, next_scene_id) or {}


def mark_junction(project_id: str, previous_scene_id: str, next_scene_id: str, status: str, **values) -> dict:
    return upsert_junction(project_id, previous_scene_id, next_scene_id, status=status, **values)
