import json
import uuid
from .db import connect

FIELDS = {
    "status", "stage", "progress", "title", "thumbnail", "duration", "channel",
    "transcript_source", "transcript", "report", "error", "provider", "model", "platform", "url",
    "vision_status", "vision_model", "vision_note", "visual_summary", "keyframes_json"
}

def create_video_job(url: str, platform: str, provider: str, model: str) -> dict:
    job_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            "INSERT INTO video_jobs(id,url,platform,provider,model) VALUES(?,?,?,?,?)",
            (job_id, url, platform, provider, model),
        )
    return get_video_job(job_id)

def _row_to_job(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    raw = item.pop("keyframes_json", "[]") or "[]"
    try:
        item["keyframes"] = json.loads(raw)
    except Exception:
        item["keyframes"] = []
    return item

def get_video_job(job_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row)

def list_video_jobs(limit: int = 40) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM video_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_job(row) for row in rows]

def update_video_job(job_id: str, **values) -> dict | None:
    clean = {k: v for k, v in values.items() if k in FIELDS}
    if not clean:
        return get_video_job(job_id)
    assignments = ", ".join(f"{key}=?" for key in clean)
    params = [*clean.values(), job_id]
    with connect() as conn:
        conn.execute(
            f"UPDATE video_jobs SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            params,
        )
    return get_video_job(job_id)

def delete_video_job(job_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM video_jobs WHERE id=?", (job_id,))
