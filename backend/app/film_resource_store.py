import base64
import hashlib
import io
import json
import re
import uuid
from pathlib import Path

from PIL import Image

from .db import connect
from .film_media_store import project_media_root
from .film_store import get_film_project


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _resource(row):
    if not row:
        return None
    data = dict(row)
    data["metadata"] = _loads(data.pop("metadata_json", None), {})
    return data


def _fingerprint(entity: dict) -> str:
    canonical = json.dumps(entity or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def list_project_resources(project_id: str, provider: str | None = None) -> list[dict]:
    with connect() as conn:
        if provider:
            rows = conn.execute(
                "SELECT * FROM film_provider_resources WHERE project_id=? AND provider=? ORDER BY resource_type,entity_id",
                (project_id, provider),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM film_provider_resources WHERE project_id=? ORDER BY provider,resource_type,entity_id",
                (project_id,),
            ).fetchall()
    return [_resource(row) for row in rows]


def get_project_resource(project_id: str, resource_type: str, entity_id: str, provider: str = "flow"):
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM film_provider_resources
            WHERE project_id=? AND provider=? AND resource_type=? AND entity_id=?""",
            (project_id, provider, resource_type, entity_id),
        ).fetchone()
    return _resource(row)


def sync_project_resources(project_id: str, provider: str = "flow") -> list[dict]:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    entities = []
    for resource_type, key in (("character", "characters"), ("location", "locations"), ("prop", "props")):
        for entity in project.get(key) or []:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or "").strip()
            if not entity_id:
                continue
            entities.append((resource_type, entity_id, entity))

    active_keys = {(kind, entity_id) for kind, entity_id, _ in entities}
    with connect() as conn:
        current_rows = conn.execute(
            "SELECT * FROM film_provider_resources WHERE project_id=? AND provider=?",
            (project_id, provider),
        ).fetchall()
        current = {(row["resource_type"], row["entity_id"]): row for row in current_rows}

        for resource_type, entity_id, entity in entities:
            fingerprint = _fingerprint(entity)
            existing = current.get((resource_type, entity_id))
            metadata = _loads(existing["metadata_json"], {}) if existing else {}
            metadata.update({
                "entity": entity,
                "descriptor_version": 2,
                "fingerprint_algorithm": "sha256-canonical-json",
            })
            if not existing:
                conn.execute(
                    """INSERT INTO film_provider_resources(
                    id,project_id,provider,resource_type,entity_id,fingerprint,status,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), project_id, provider, resource_type, entity_id,
                        fingerprint, "pending", json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                continue

            changed = existing["fingerprint"] != fingerprint
            if changed:
                next_status = "stale" if (existing["provider_ref"] or existing["local_path"]) else "pending"
            elif existing["status"] == "retired":
                next_status = "ready" if (existing["provider_ref"] or existing["local_path"]) else "pending"
            else:
                next_status = existing["status"]

            provider_ref = existing["provider_ref"]
            flow_media_id = str(metadata.get("flow_media_id") or "").strip()
            if (
                provider == "flow"
                and flow_media_id
                and (not provider_ref or str(provider_ref).startswith("local-canonical://"))
            ):
                provider_ref = flow_media_id
                metadata["provider_ref_kind"] = "flow_media_id"
                metadata["provider_ref_normalized"] = True

            conn.execute(
                """UPDATE film_provider_resources
                SET fingerprint=?,status=?,provider_ref=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (
                    fingerprint,
                    next_status,
                    provider_ref,
                    json.dumps(metadata, ensure_ascii=False),
                    existing["id"],
                ),
            )

        for (resource_type, entity_id), row in current.items():
            if (resource_type, entity_id) not in active_keys and row["status"] != "retired":
                conn.execute(
                    "UPDATE film_provider_resources SET status='retired',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["id"],),
                )

    return list_project_resources(project_id, provider)


def update_resource_binding(
    project_id: str,
    resource_type: str,
    entity_id: str,
    *,
    provider: str = "flow",
    provider_ref: str | None = None,
    local_path: str | None = None,
    status: str | None = None,
    error: str | None = None,
    metadata_patch: dict | None = None,
):
    resource = get_project_resource(project_id, resource_type, entity_id, provider)
    if not resource:
        sync_project_resources(project_id, provider)
        resource = get_project_resource(project_id, resource_type, entity_id, provider)
    if not resource:
        raise ValueError("Resource không tồn tại trong Story Bible.")

    allowed_status = {"pending", "ready", "locked", "stale", "error", "retired"}
    next_status = status or resource["status"]
    if next_status not in allowed_status:
        raise ValueError("Resource status không hợp lệ.")

    metadata = dict(resource.get("metadata") or {})
    if metadata_patch:
        metadata.update(metadata_patch)

    with connect() as conn:
        conn.execute(
            """UPDATE film_provider_resources
            SET provider_ref=COALESCE(?,provider_ref),local_path=COALESCE(?,local_path),
                status=?,error=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (
                provider_ref, local_path, next_status, error,
                json.dumps(metadata, ensure_ascii=False), resource["id"],
            ),
        )
    return get_project_resource(project_id, resource_type, entity_id, provider)


def save_canonical_asset(
    project_id: str,
    resource_type: str,
    entity_id: str,
    data_url: str,
    filename: str | None = None,
    provider: str = "flow",
    status: str = "ready",
):
    if resource_type not in {"character", "location", "prop"}:
        raise ValueError("Resource type không hợp lệ.")
    if status not in {"pending", "ready"}:
        raise ValueError("Canonical asset status chỉ được pending hoặc ready.")
    resource = get_project_resource(project_id, resource_type, entity_id, provider)
    if not resource:
        sync_project_resources(project_id, provider)
        resource = get_project_resource(project_id, resource_type, entity_id, provider)
    if not resource:
        raise ValueError("Resource không tồn tại trong Story Bible.")
    if resource.get("status") == "locked":
        raise ValueError("CANONICAL_ASSET_LOCKED: Tài nguyên đã khóa cho render; không thể thay ảnh chuẩn.")

    raw = (data_url or "").strip()
    if "," in raw and raw.lower().startswith("data:image/"):
        raw = raw.split(",", 1)[1]
    try:
        payload = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("Ảnh canonical không phải base64 hợp lệ.") from exc
    if not payload or len(payload) > 15 * 1024 * 1024:
        raise ValueError("Ảnh canonical rỗng hoặc vượt quá 15 MB.")

    try:
        image = Image.open(io.BytesIO(payload))
        image.verify()
        image = Image.open(io.BytesIO(payload)).convert("RGB")
    except Exception as exc:
        raise ValueError("File canonical không phải ảnh hợp lệ.") from exc

    folder = project_media_root(project_id) / "film_assets" / resource_type / entity_id
    folder.mkdir(parents=True, exist_ok=True)
    metadata = dict(resource.get("metadata") or {})

    normalized = io.BytesIO()
    image.save(normalized, "JPEG", quality=94, optimize=True)
    canonical_bytes = normalized.getvalue()
    sha256 = hashlib.sha256(canonical_bytes).hexdigest()

    existing_path = Path(str(resource.get("local_path") or ""))
    existing_sha256 = str(metadata.get("canonical_sha256") or "").strip()
    if existing_sha256 == sha256 and existing_path.exists() and existing_path.is_file():
        metadata.update({
            "canonical_sha256": sha256,
            "source_filename": filename or metadata.get("source_filename"),
            "canonical_mime": "image/jpeg",
            "canonical_width": image.width,
            "canonical_height": image.height,
            "canonical_asset_url": f"/api/film/projects/{project_id}/resources/{resource_type}/{entity_id}/asset",
            "visual_lock": "QC_PENDING" if status == "pending" else "READY",
            "content_hash_algorithm": "sha256-normalized-jpeg",
            "content_deduplicated": True,
            "content_cache_hit_count": int(metadata.get("content_cache_hit_count") or 0) + 1,
        })
        return update_resource_binding(
            project_id,
            resource_type,
            entity_id,
            provider=provider,
            local_path=str(existing_path),
            status=status,
            error=None,
            metadata_patch=metadata,
        )

    version = int(metadata.get("asset_version") or 0) + 1
    safe_entity = re.sub(r"[^A-Za-z0-9_.-]+", "_", entity_id).strip("._") or "entity"
    canonical_name = f"{resource_type}_{safe_entity}_v{version}.jpg"
    target = folder / canonical_name
    target.write_bytes(canonical_bytes)
    metadata.update({
        "asset_version": version,
        "canonical_sha256": sha256,
        "canonical_filename": canonical_name,
        "source_filename": filename or None,
        "canonical_mime": "image/jpeg",
        "canonical_width": image.width,
        "canonical_height": image.height,
        "canonical_asset_url": f"/api/film/projects/{project_id}/resources/{resource_type}/{entity_id}/asset",
        "visual_lock": "QC_PENDING" if status == "pending" else "READY",
        "content_hash_algorithm": "sha256-normalized-jpeg",
        "content_deduplicated": False,
    })
    saved = update_resource_binding(
        project_id,
        resource_type,
        entity_id,
        provider=provider,
        provider_ref=f"local-canonical://{project_id}/{resource_type}/{entity_id}/v{version}",
        local_path=str(target),
        status=status,
        error=None,
        metadata_patch=metadata,
    )
    try:
        from .film_media_service import register_canonical_from_resource
        register_canonical_from_resource(saved)
    except Exception:
        pass
    try:
        from .film_acceptance_snapshot import propagate_canonical_change
        propagate_canonical_change(project_id, resource_type, entity_id)
    except Exception:
        pass
    return saved


def lock_project_resources(project_id: str, provider: str = "flow") -> list[dict]:
    sync_project_resources(project_id, provider)
    resources = list_project_resources(project_id, provider)
    with connect() as conn:
        for resource in resources:
            if resource.get("status") == "retired":
                continue
            path = Path(str(resource.get("local_path") or ""))
            has_asset = bool(resource.get("local_path")) and path.exists()
            metadata = dict(resource.get("metadata") or {})
            qc = metadata.get("canonical_qc") if isinstance(metadata.get("canonical_qc"), dict) else {}
            qc_passed = bool((qc.get("hard_gate") or {}).get("passed") is True)
            if has_asset and qc_passed:
                metadata["visual_lock"] = "LOCKED"
                conn.execute(
                    """UPDATE film_provider_resources
                    SET status='locked',metadata_json=?,error=NULL,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (json.dumps(metadata, ensure_ascii=False), resource["id"]),
                )
            elif has_asset and not qc_passed:
                metadata["visual_lock"] = "QC_REQUIRED"
                conn.execute(
                    """UPDATE film_provider_resources
                    SET status='pending',metadata_json=?,error=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (json.dumps(metadata, ensure_ascii=False), "CANONICAL_QC_REQUIRED: Ảnh chưa vượt qua Canonical Vision QC.", resource["id"]),
                )
            elif resource.get("status") not in {"stale", "error"}:
                metadata["visual_lock"] = "MISSING"
                conn.execute(
                    """UPDATE film_provider_resources
                    SET status='pending',metadata_json=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (json.dumps(metadata, ensure_ascii=False), resource["id"]),
                )
    return list_project_resources(project_id, provider)


def scene_resource_manifest(project: dict, scene: dict, provider: str = "flow") -> dict:
    rows = list_project_resources(project["id"], provider)
    by_key = {(row["resource_type"], row["entity_id"]): row for row in rows}
    characters = [
        by_key.get(("character", str(entity_id)))
        for entity_id in (scene.get("characters") or [])
    ]
    location = by_key.get(("location", str(scene.get("location_id")))) if scene.get("location_id") else None
    props = [
        by_key.get(("prop", str(entity_id)))
        for entity_id in (scene.get("props_present") or [])
    ]
    required = [row for row in characters if row]
    if location:
        required.append(location)
    required.extend(row for row in props if row)
    ready_statuses = {"ready", "locked"}
    missing = []
    for row in required:
        local_path = str(row.get("local_path") or "")
        metadata = row.get("metadata") or {}
        qc = metadata.get("canonical_qc") if isinstance(metadata.get("canonical_qc"), dict) else {}
        qc_passed = bool((qc.get("hard_gate") or {}).get("passed") is True)
        if row.get("status") not in ready_statuses or not local_path or not Path(local_path).exists() or not qc_passed:
            suffix = "" if qc_passed else ":qc-not-passed"
            missing.append(f"{row.get('resource_type')}:{row.get('entity_id')}{suffix}")
    if len([row for row in characters if row]) != len(scene.get("characters") or []):
        missing.append("character:missing-resource-record")
    if scene.get("location_id") and not location:
        missing.append(f"location:{scene.get('location_id')}")
    if len([row for row in props if row]) != len(scene.get("props_present") or []):
        missing.append("prop:missing-resource-record")
    def _prop_critical(entity_id: str) -> bool:
        transfers = scene.get("prop_transfers") or []
        continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
        critical_ids = {str(x) for x in (continuity.get("critical_props") or [])}
        if str(entity_id) in critical_ids:
            return True
        for item in transfers:
            if not isinstance(item, dict):
                continue
            if str(item.get("prop_id") or item.get("entity_id") or "") == str(entity_id):
                return True
        return False

    references = []
    for row in required:
        kind = str(row.get("resource_type") or "")
        entity_id = row.get("entity_id")
        critical = kind == "character" or kind == "location" or (kind == "prop" and _prop_critical(entity_id))
        references.append({
            "resource_type": kind,
            "entity_id": entity_id,
            "status": row.get("status"),
            "local_path": row.get("local_path"),
            "provider_ref": row.get("provider_ref"),
            "asset_version": (row.get("metadata") or {}).get("asset_version"),
            "canonical_sha256": (row.get("metadata") or {}).get("canonical_sha256"),
            "required": True,
            "critical": critical,
        })
    return {
        "characters": [row for row in characters if row],
        "location": location,
        "props": [row for row in props if row],
        "references": references,
        "missing": missing,
        "ready": not missing,
        "version": "visual-continuity-v2",
    }
