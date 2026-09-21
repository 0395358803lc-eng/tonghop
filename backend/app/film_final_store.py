from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import connect

FINAL_STATUSES = (
    "ASSEMBLY_PENDING",
    "ASSEMBLING",
    "ASSEMBLY_FAILED",
    "QC_PENDING",
    "QC_RUNNING",
    "QC_FAILED",
    "APPROVED",
    "STALE",
)


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
    data["manifest"] = _loads(data.pop("manifest_json", None), {})
    data["qc"] = _loads(data.pop("qc_json", None), {})
    data["version"] = int(data.get("version") or 1)
    return data


def list_final_renders(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_final_renders WHERE project_id=? ORDER BY version DESC, created_at DESC",
            (project_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def get_final_render(project_id: str, render_id: str | None = None) -> dict | None:
    with connect() as conn:
        if render_id:
            row = conn.execute(
                "SELECT * FROM film_final_renders WHERE project_id=? AND id=?",
                (project_id, render_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM film_final_renders WHERE project_id=? ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
    return _row(row)


def next_final_version(project_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM film_final_renders WHERE project_id=?",
            (project_id,),
        ).fetchone()
    return int((row["v"] if row and row["v"] is not None else 0) + 1)


def create_final_render(project_id: str, **values) -> dict:
    rid = values.get("id") or str(uuid.uuid4())
    version = int(values.get("version") or next_final_version(project_id))
    status = values.get("status") or "ASSEMBLY_PENDING"
    if status not in FINAL_STATUSES:
        status = "ASSEMBLY_PENDING"
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_final_renders(
                 id,project_id,version,status,media_id,manifest_json,manifest_hash,error,qc_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid, project_id, version, status, values.get("media_id"),
                json.dumps(values.get("manifest") or {}, ensure_ascii=False),
                values.get("manifest_hash"), values.get("error"),
                json.dumps(values.get("qc") or {}, ensure_ascii=False), now, now,
            ),
        )
    return get_final_render(project_id, rid)


def update_final_render(project_id: str, render_id: str, **values) -> dict:
    current = get_final_render(project_id, render_id)
    if not current:
        raise ValueError("Không tìm thấy final render.")
    status = values.get("status", current.get("status"))
    if status not in FINAL_STATUSES:
        status = current.get("status") or "ASSEMBLY_PENDING"
    manifest = values["manifest"] if "manifest" in values else current.get("manifest") or {}
    qc = values["qc"] if "qc" in values else current.get("qc") or {}
    with connect() as conn:
        conn.execute(
            """UPDATE film_final_renders SET status=?, media_id=?, manifest_json=?, manifest_hash=?, error=?, qc_json=?, updated_at=?
               WHERE project_id=? AND id=?""",
            (
                status,
                values["media_id"] if "media_id" in values else current.get("media_id"),
                json.dumps(manifest, ensure_ascii=False),
                values["manifest_hash"] if "manifest_hash" in values else current.get("manifest_hash"),
                values["error"] if "error" in values else current.get("error"),
                json.dumps(qc, ensure_ascii=False),
                _now(), project_id, render_id,
            ),
        )
    return get_final_render(project_id, render_id)
