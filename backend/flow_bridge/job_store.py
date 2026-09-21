import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR

JOBS_PATH = DATA_DIR / "flow_bridge_jobs.json"
MAX_PERSISTED_JOBS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jobs() -> dict[str, dict]:
    if not JOBS_PATH.exists():
        return {}
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_jobs(jobs: dict[str, dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if len(jobs) > MAX_PERSISTED_JOBS:
        active = {k: v for k, v in jobs.items() if v.get("status") in {"queued", "preparing", "generating"}}
        finished = [(k, v) for k, v in jobs.items() if k not in active]
        finished.sort(key=lambda item: str(item[1].get("updated_at") or ""), reverse=True)
        keep = dict(finished[:max(0, MAX_PERSISTED_JOBS - len(active))])
        keep.update(active)
        jobs.clear()
        jobs.update(keep)
    temp = JOBS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(JOBS_PATH)


def recover_interrupted_jobs(jobs: dict[str, dict]) -> int:
    changed = 0
    for job in jobs.values():
        if job.get("status") in {"queued", "preparing", "generating"}:
            job.update({
                "status": "failed",
                "progress": 100,
                "error": "Flow Bridge đã restart trước khi job hoàn tất.",
                "recovered_after_restart": True,
                "updated_at": _now(),
            })
            changed += 1
    if changed:
        save_jobs(jobs)
    return changed


def put_job(jobs: dict[str, dict], job_id: str, job: dict) -> dict:
    payload = dict(job)
    payload.setdefault("created_at", _now())
    payload["updated_at"] = _now()
    jobs[job_id] = payload
    save_jobs(jobs)
    return payload


def patch_job(jobs: dict[str, dict], job_id: str, **values) -> dict:
    job = jobs[job_id]
    job.update(values)
    job["updated_at"] = _now()
    save_jobs(jobs)
    return job
