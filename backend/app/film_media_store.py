import json
import re
import uuid
from pathlib import Path

from .config import DATA_DIR, LEGACY_MEDIA_DIRS, MEDIA_DIR
from .db import connect

MEDIA_SCHEMA_VERSION = 1
ALLOWED_MEDIA_TYPES = {"image", "video"}
ALLOWED_ROLES = {"canonical_image", "scene_image", "scene_video", "final_video", "repair_candidate"}
ALLOWED_STATUSES = {"pending", "processing", "completed", "failed", "retired"}
ALLOWED_QC = {"not_run", "pending", "passed", "failed", "error"}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def media_root() -> Path:
    return MEDIA_DIR.resolve()


def project_media_root(project_id: str, root: Path | None = None) -> Path:
    safe = str(project_id or "").strip()
    if not safe or any(char in safe for char in ("/", "\\", ":")) or safe in {".", ".."}:
        raise ValueError("PROJECT_MEDIA_ID_INVALID")
    base = (root or MEDIA_DIR).resolve()
    return base / "projects" / safe


def media_roots() -> tuple[Path, ...]:
    roots = [MEDIA_DIR.resolve()]
    for legacy in LEGACY_MEDIA_DIRS:
        resolved = legacy.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


LEGACY_DATA_MEDIA_DIRS = {
    "film_assets",
    "flow_downloads",
    "flow_image_downloads",
    "generated_media",
    "narrator_tts",
    "final_films",
}


def _is_allowed_media_path(resolved: Path) -> bool:
    current_root = MEDIA_DIR.resolve()
    if resolved.is_relative_to(current_root):
        return True

    data_root = DATA_DIR.resolve()
    for legacy in LEGACY_MEDIA_DIRS:
        legacy_root = legacy.resolve()
        if not resolved.is_relative_to(legacy_root):
            continue
        if legacy_root == data_root:
            rel = resolved.relative_to(legacy_root)
            return bool(rel.parts) and rel.parts[0] in LEGACY_DATA_MEDIA_DIRS
        return True
    return False


def validate_media_path(path_value: str | Path | None) -> Path:
    if not path_value:
        raise ValueError("MEDIA_PATH_MISSING: Thiếu đường dẫn file media.")
    raw = Path(str(path_value))
    if ".." in raw.parts:
        raise ValueError("MEDIA_PATH_DENIED: Path traversal bị từ chối.")
    root = media_root()
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if not _is_allowed_media_path(resolved):
        raise ValueError("MEDIA_PATH_DENIED: File nằm ngoài các media root được phép.")
    if not resolved.is_file():
        raise ValueError("MEDIA_FILE_MISSING: File media không tồn tại trên đĩa.")
    return resolved


def output_key_for(*, role: str, scene_id: str | None = None, resource_type: str | None = None, entity_id: str | None = None) -> str:
    if role in {"canonical_image", "repair_candidate"} and resource_type and entity_id:
        return f"canonical:{resource_type}:{entity_id}"
    if role in {"scene_video", "repair_candidate"} and scene_id:
        return f"scene_video:{scene_id}" if role == "scene_video" else f"scene_video:{scene_id}"
    if role == "scene_image" and scene_id:
        return f"scene_image:{scene_id}"
    if role == "final_video":
        return "final_video"
    if scene_id:
        return f"scene:{scene_id}:{role}"
    if resource_type and entity_id:
        return f"{role}:{resource_type}:{entity_id}"
    return role


def public_media(row: dict | None) -> dict | None:
    if not row:
        return None
    data = dict(row)
    media_id = data["id"]
    metadata = dict(data.get("metadata") or {})
    for key in ("local_path", "file_path", "thumbnail_path", "windows_path", "abs_path"):
        metadata.pop(key, None)
    qc = data.get("qc") if isinstance(data.get("qc"), dict) else _loads(data.get("qc_json"), {})
    return {
        "id": media_id,
        "project_id": data.get("project_id"),
        "scene_id": data.get("scene_id"),
        "resource_type": data.get("resource_type"),
        "entity_id": data.get("entity_id"),
        "output_key": data.get("output_key"),
        "media_type": data.get("media_type"),
        "role": data.get("role"),
        "status": data.get("status"),
        "provider": data.get("provider"),
        "model": data.get("model"),
        "provider_job_id": data.get("provider_job_id"),
        "file_url": f"/api/film/media/{media_id}/file" if data.get("file_path") else None,
        "thumbnail_url": f"/api/film/media/{media_id}/thumbnail" if data.get("thumbnail_path") else None,
        "download_name": metadata.get("download_name") or Path(str(data.get("file_path") or "media.bin")).name,
        "mime_type": data.get("mime_type"),
        "file_size": data.get("file_size"),
        "width": data.get("width"),
        "height": data.get("height"),
        "duration_seconds": data.get("duration_seconds"),
        "version": int(data.get("version") or 1),
        "is_selected": bool(data.get("is_selected")),
        "qc_status": data.get("qc_status") or "not_run",
        "qc_score": data.get("qc_score"),
        "qc": qc,
        "metadata": metadata,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "schema_version": MEDIA_SCHEMA_VERSION,
    }


def _row(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    data["metadata"] = _loads(data.pop("metadata_json", None), {})
    data["qc"] = _loads(data.pop("qc_json", None), {})
    data["is_selected"] = bool(data.get("is_selected"))
    data["version"] = int(data.get("version") or 1)
    return data


def create_media(
    *,
    project_id: str,
    media_type: str,
    role: str,
    scene_id: str | None = None,
    resource_type: str | None = None,
    entity_id: str | None = None,
    status: str = "pending",
    provider: str | None = None,
    model: str | None = None,
    provider_job_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError("MEDIA_TYPE_INVALID")
    if role not in ALLOWED_ROLES:
        raise ValueError("MEDIA_ROLE_INVALID")
    if status not in ALLOWED_STATUSES:
        raise ValueError("MEDIA_STATUS_INVALID")
    key = output_key_for(role=role, scene_id=scene_id, resource_type=resource_type, entity_id=entity_id)
    media_id = str(uuid.uuid4())
    with connect() as conn:
        version = _next_version(conn, project_id, key)
        conn.execute(
            """INSERT INTO film_generated_media(
            id,project_id,scene_id,resource_type,entity_id,output_key,media_type,role,status,
            provider,model,provider_job_id,version,is_selected,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            (
                media_id, project_id, scene_id, resource_type, entity_id, key, media_type, role, status,
                provider, model, provider_job_id, version, json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
    return get_media(media_id)


def get_media(media_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_generated_media WHERE id=?", (media_id,)).fetchone()
    return _row(row)


def get_media_by_provider_job(provider_job_id: str) -> dict | None:
    if not provider_job_id:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_generated_media WHERE provider_job_id=? ORDER BY created_at DESC LIMIT 1",
            (provider_job_id,),
        ).fetchone()
    return _row(row)


def list_project_media(
    project_id: str,
    scene_id: str | None = None,
    media_type: str | None = None,
    role: str | None = None,
    status: str | None = None,
    selected_only: bool = False,
) -> list[dict]:
    clauses = ["project_id=?"]
    args: list = [project_id]
    if scene_id:
        clauses.append("scene_id=?")
        args.append(scene_id)
    if media_type:
        clauses.append("media_type=?")
        args.append(media_type)
    if role:
        clauses.append("role=?")
        args.append(role)
    if status:
        clauses.append("status=?")
        args.append(status)
    if selected_only:
        clauses.append("is_selected=1")
    sql = f"SELECT * FROM film_generated_media WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, version DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row(row) for row in rows]


def get_selected_media(project_id: str, output_key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_generated_media WHERE project_id=? AND output_key=? AND is_selected=1 ORDER BY version DESC LIMIT 1",
            (project_id, output_key),
        ).fetchone()
    return _row(row)


def list_media_versions(media_id: str) -> list[dict]:
    current = get_media(media_id)
    if not current:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_generated_media WHERE project_id=? AND output_key=? ORDER BY version DESC, created_at DESC",
            (current["project_id"], current["output_key"]),
        ).fetchall()
    return [_row(row) for row in rows]


def mark_media_failed(media_id: str, error: str, metadata_patch: dict | None = None) -> dict | None:
    current = get_media(media_id)
    if not current:
        return None
    metadata = dict(current.get("metadata") or {})
    if metadata_patch:
        metadata.update(metadata_patch)
    metadata["error"] = error
    with connect() as conn:
        conn.execute(
            "UPDATE film_generated_media SET status='failed', metadata_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False), media_id),
        )
    return get_media(media_id)


def media_is_selectable(media: dict | None) -> dict:
    if not media:
        raise ValueError("MEDIA_NOT_FOUND")
    if media.get("status") != "completed":
        raise ValueError("MEDIA_NOT_SELECTABLE: Chỉ chọn được bản đã hoàn tất và có file local.")
    if media.get("role") == "repair_candidate":
        raise ValueError("MEDIA_NOT_SELECTABLE: Bản QC chưa đạt không thể chọn làm bản đang dùng.")
    if media.get("qc_status") != "passed":
        raise ValueError("MEDIA_NOT_SELECTABLE: Chỉ chọn được bản đã QC đạt.")
    validate_media_path(media.get("file_path"))
    return media


def update_media_qc(media_id: str, *, qc_status: str, qc: dict | None = None, qc_score: float | None = None) -> dict:
    if qc_status not in ALLOWED_QC:
        raise ValueError("MEDIA_QC_STATUS_INVALID")
    with connect() as conn:
        conn.execute(
            "UPDATE film_generated_media SET qc_status=?, qc_score=?, qc_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (qc_status, qc_score, json.dumps(qc or {}, ensure_ascii=False), media_id),
        )
    return get_media(media_id)


def select_media(media_id: str) -> dict:
    current = media_is_selectable(get_media(media_id))
    with connect() as conn:
        conn.execute(
            "UPDATE film_generated_media SET is_selected=0, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND output_key=? AND id<>?",
            (current["project_id"], current["output_key"], media_id),
        )
        conn.execute(
            "UPDATE film_generated_media SET is_selected=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (media_id,),
        )
    return get_media(media_id)


def register_completed_media(
    *,
    project_id: str,
    media_type: str,
    role: str,
    file_path: str | Path,
    scene_id: str | None = None,
    resource_type: str | None = None,
    entity_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    provider_job_id: str | None = None,
    thumbnail_path: str | Path | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: float | None = None,
    qc_status: str = "not_run",
    qc_score: float | None = None,
    qc: dict | None = None,
    metadata: dict | None = None,
    select_if_passed: bool = True,
) -> dict:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError("MEDIA_TYPE_INVALID")
    if role not in ALLOWED_ROLES:
        raise ValueError("MEDIA_ROLE_INVALID")
    if qc_status not in ALLOWED_QC:
        raise ValueError("MEDIA_QC_STATUS_INVALID")
    resolved = validate_media_path(file_path)
    thumb = None
    if thumbnail_path:
        try:
            thumb = validate_media_path(thumbnail_path)
        except ValueError:
            thumb = None
    key = output_key_for(role="canonical_image" if resource_type and entity_id else role, scene_id=scene_id, resource_type=resource_type, entity_id=entity_id)
    if scene_id and media_type == "video":
        key = f"scene_video:{scene_id}"
    elif resource_type and entity_id:
        key = f"canonical:{resource_type}:{entity_id}"
    existing = get_media_by_provider_job(provider_job_id) if provider_job_id else None
    is_selected = bool(select_if_passed and qc_status == "passed" and role != "repair_candidate")
    payload = {
        "scene_id": scene_id,
        "resource_type": resource_type,
        "entity_id": entity_id,
        "output_key": key,
        "media_type": media_type,
        "role": role,
        "status": "completed",
        "provider": provider,
        "model": model,
        "provider_job_id": provider_job_id,
        "file_path": str(resolved),
        "thumbnail_path": str(thumb) if thumb else None,
        "mime_type": mime_type,
        "file_size": int(file_size or resolved.stat().st_size),
        "width": width,
        "height": height,
        "duration_seconds": duration_seconds,
        "qc_status": qc_status,
        "qc_score": qc_score,
        "qc_json": json.dumps(qc or {}, ensure_ascii=False),
        "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
    }
    with connect() as conn:
        if existing:
            media_id = existing["id"]
            parts = [f"{column}=?" for column in payload]
            conn.execute(
                f"UPDATE film_generated_media SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                list(payload.values()) + [media_id],
            )
        else:
            media_id = str(uuid.uuid4())
            version = _next_version(conn, project_id, key)
            conn.execute(
                """INSERT INTO film_generated_media(
                id,project_id,scene_id,resource_type,entity_id,output_key,media_type,role,status,
                provider,model,provider_job_id,file_path,thumbnail_path,mime_type,file_size,width,height,
                duration_seconds,version,is_selected,qc_status,qc_score,qc_json,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    media_id, project_id, payload["scene_id"], payload["resource_type"], payload["entity_id"],
                    key, media_type, role, "completed", provider, model, provider_job_id,
                    payload["file_path"], payload["thumbnail_path"], mime_type, payload["file_size"],
                    width, height, duration_seconds, version, 0, qc_status, qc_score,
                    payload["qc_json"], payload["metadata_json"],
                ),
            )
        if is_selected:
            conn.execute(
                "UPDATE film_generated_media SET is_selected=0, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND output_key=? AND id<>?",
                (project_id, key, media_id),
            )
            conn.execute("UPDATE film_generated_media SET is_selected=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (media_id,))
    return get_media(media_id)


def bind_resource_media(project_id: str, resource_type: str, entity_id: str, media_id: str, provider: str = "flow") -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE film_provider_resources SET media_id=?, updated_at=CURRENT_TIMESTAMP
            WHERE project_id=? AND provider=? AND resource_type=? AND entity_id=?""",
            (media_id, project_id, provider, resource_type, entity_id),
        )


def bind_render_job_media(job_id: str, media_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE film_render_jobs SET media_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (media_id, job_id),
        )


def _next_version(conn, project_id: str, output_key: str) -> int:
    row = conn.execute(
        "SELECT MAX(version) AS version FROM film_generated_media WHERE project_id=? AND output_key=?",
        (project_id, output_key),
    ).fetchone()
    current = int(row["version"] or 0) if row and row["version"] is not None else 0
    return current + 1


def assert_media_id(media_id: str) -> str:
    try:
        return str(uuid.UUID(str(media_id)))
    except Exception as exc:
        raise ValueError("MEDIA_ID_INVALID") from exc
