from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .db import connect
from .film_boundary_store import list_junctions, mark_junction
from .film_dialogue_service import scene_dialogue_requirements
from .film_final_store import list_final_renders, update_final_render
from .film_media_store import get_media, get_selected_media, output_key_for
from .film_resource_store import list_project_resources
from .film_scene_state_store import get_ledger, get_scene_state, list_scene_states, upsert_scene_state
from .film_store import get_film_project
from .film_voice_profile_store import get_scene_audio_requirements, list_voice_profiles


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(payload: dict) -> str:
    body = {key: value for key, value in (payload or {}).items() if key not in {"id", "created_at", "snapshot_hash"}}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _row(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    data["payload"] = _loads(data.pop("payload_json", None), {})
    return data


def public_snapshot(row: dict | None) -> dict | None:
    if not row:
        return None
    payload = dict(row.get("payload") or {})
    payload.pop("file_path_internal", None)
    for item in payload.get("canonical") or []:
        if isinstance(item, dict):
            item.pop("local_path", None)
    return {
        "id": row.get("id"),
        "project_id": row.get("project_id"),
        "scene_id": row.get("scene_id"),
        "snapshot_hash": row.get("snapshot_hash"),
        "created_at": row.get("created_at"),
        "payload": payload,
    }


def _scene_of(project: dict, scene_id: str) -> dict:
    return next((item for item in (project.get("scenes") or []) if item.get("id") == scene_id), {})


def _selected_video(project_id: str, scene_id: str, state: dict | None = None):
    key = output_key_for(role="scene_video", scene_id=scene_id)
    selected = get_selected_media(project_id, key)
    if not selected and (state or {}).get("selected_media_id"):
        selected = get_media(state["selected_media_id"])
    return selected


def _voice_ids_for_scene(scene: dict) -> set[str]:
    voice_ids = {str(x) for x in (scene.get("characters") or [])}
    if str(scene.get("voiceover") or "").strip():
        voice_ids.add("NARRATOR")
    return voice_ids


def current_fingerprint(project_id: str, scene_id: str) -> dict:
    project = get_film_project(project_id) or {}
    scene = _scene_of(project, scene_id)
    state = get_scene_state(project_id, scene_id) or {}
    media = _selected_video(project_id, scene_id, state)
    resources = list_project_resources(project_id, "flow")
    used_ids = set(str(x) for x in (scene.get("characters") or []))
    if scene.get("location_id"):
        used_ids.add(str(scene.get("location_id")))
    used_ids.update(str(x) for x in (scene.get("props_present") or []))
    canonical = []
    for row in resources:
        if str(row.get("entity_id")) not in used_ids:
            continue
        meta = row.get("metadata") or {}
        canonical.append({
            "resource_type": row.get("resource_type"),
            "entity_id": row.get("entity_id"),
            "status": row.get("status"),
            "version": meta.get("asset_version"),
            "canonical_sha256": meta.get("canonical_sha256"),
            "media_id": meta.get("media_id") or meta.get("selected_media_id"),
        })
    voices = []
    voice_ids = _voice_ids_for_scene(scene)
    for profile in list_voice_profiles(project_id):
        if str(profile.get("character_id")) in voice_ids:
            voices.append({
                "character_id": profile.get("character_id"),
                "provider_voice_id": profile.get("provider_voice_id"),
                "profile": profile.get("profile") or {},
            })
    audio = get_scene_audio_requirements(project_id, scene_id) or scene_dialogue_requirements(project, scene)
    return {
        "scene_id": scene_id,
        "prompt": scene.get("flow_prompt") or scene.get("visual_prompt") or "",
        "dialogue": scene.get("dialogue") or [],
        "voiceover": scene.get("voiceover") or "",
        "canonical": sorted(canonical, key=lambda x: (str(x.get("resource_type")), str(x.get("entity_id")))),
        "voices": sorted(voices, key=lambda x: str(x.get("character_id"))),
        "audio_requirements": {
            "speech_required": bool(audio.get("speech_required")),
            "dialogue": audio.get("dialogue") or [],
            "voiceover": audio.get("voiceover") or [],
        },
        "selected_media_id": (media or {}).get("id") or state.get("selected_media_id"),
        "selected_media_version": int((media or {}).get("version") or 0),
    }


def build_snapshot_payload(project_id: str, scene_id: str) -> dict:
    project = get_film_project(project_id) or {}
    scene = _scene_of(project, scene_id)
    state = get_scene_state(project_id, scene_id) or {}
    media = _selected_video(project_id, scene_id, state)
    ledger = get_ledger(project_id, scene_id) or {}
    print_fp = current_fingerprint(project_id, scene_id)
    return {
        "scene_id": scene_id,
        "scene_index": int(scene.get("scene_index") or state.get("scene_index") or 0),
        "scene_version": int((media or {}).get("version") or 1),
        "selected_media_id": print_fp.get("selected_media_id"),
        "selected_media_version": print_fp.get("selected_media_version"),
        "canonical": print_fp.get("canonical") or [],
        "canonical_media_ids": [item.get("media_id") for item in (print_fp.get("canonical") or []) if item.get("media_id")],
        "prompt": print_fp.get("prompt"),
        "provider": (media or {}).get("provider") or ((state.get("snapshot") or {}).get("provider")),
        "model": (media or {}).get("model"),
        "qc": state.get("qc") or (media or {}).get("qc") or {},
        "first_frame": ledger.get("accepted_first_frame") or ((media or {}).get("metadata") or {}).get("first_frame_url"),
        "last_frame": ledger.get("accepted_last_frame") or ((media or {}).get("metadata") or {}).get("last_frame_url"),
        "ledger": {
            "selected_media_id": ledger.get("selected_media_id"),
            "accepted_first_frame": ledger.get("accepted_first_frame"),
            "accepted_last_frame": ledger.get("accepted_last_frame"),
            "location_id": ledger.get("location_id"),
            "prop_holder": ledger.get("prop_holder"),
            "prop_state": ledger.get("prop_state"),
        },
        "voice_requirements": print_fp.get("voices") or [],
        "audio_requirements": print_fp.get("audio_requirements") or {},
        "dialogue": print_fp.get("dialogue") or [],
        "fingerprint": snapshot_hash(print_fp),
    }


def create_acceptance_snapshot(project_id: str, scene_id: str) -> dict:
    payload = build_snapshot_payload(project_id, scene_id)
    digest = snapshot_hash(payload)
    payload["snapshot_hash"] = digest
    now = _now()
    payload["created_at"] = now
    sid = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_acceptance_snapshots(id,project_id,scene_id,snapshot_hash,payload_json,created_at)
               VALUES(?,?,?,?,?,?)""",
            (sid, project_id, scene_id, digest, json.dumps(payload, ensure_ascii=False), now),
        )
    return public_snapshot(get_acceptance_snapshot(project_id, sid)) or {}


def list_acceptance_snapshots(project_id: str, scene_id: str | None = None) -> list[dict]:
    with connect() as conn:
        if scene_id:
            rows = conn.execute(
                "SELECT * FROM film_acceptance_snapshots WHERE project_id=? AND scene_id=? ORDER BY created_at DESC",
                (project_id, scene_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM film_acceptance_snapshots WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
    return [public_snapshot(_row(row)) for row in rows]


def get_acceptance_snapshot(project_id: str, snapshot_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_acceptance_snapshots WHERE project_id=? AND id=?",
            (project_id, snapshot_id),
        ).fetchone()
    return _row(row)


def latest_acceptance_snapshot(project_id: str, scene_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_acceptance_snapshots WHERE project_id=? AND scene_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id, scene_id),
        ).fetchone()
    return _row(row)


def snapshot_fingerprint_mismatch(project_id: str, scene_id: str) -> bool:
    latest = latest_acceptance_snapshot(project_id, scene_id)
    if not latest:
        return False
    stored = (latest.get("payload") or {}).get("fingerprint")
    current = snapshot_hash(current_fingerprint(project_id, scene_id))
    return bool(stored and stored != current)


def mark_final_stale(project_id: str, reason: str) -> list[dict]:
    changed = []
    for row in list_final_renders(project_id):
        if row.get("status") in {"ASSEMBLY_PENDING", "ASSEMBLING", "ASSEMBLY_FAILED"}:
            continue
        if row.get("status") == "STALE":
            continue
        changed.append(update_final_render(project_id, row["id"], status="STALE", error=reason))
    return changed


def mark_junctions_stale_for_scene(project_id: str, scene_id: str, reason: str) -> list[dict]:
    changed = []
    for row in list_junctions(project_id):
        if row.get("previous_scene_id") != scene_id and row.get("next_scene_id") != scene_id:
            continue
        if row.get("status") in {"STALE", "PENDING"}:
            continue
        changed.append(mark_junction(project_id, row["previous_scene_id"], row["next_scene_id"], "STALE", error=reason))
    return changed


def mark_scene_stale(project_id: str, scene_id: str, reason: str) -> dict | None:
    state = get_scene_state(project_id, scene_id) or {}
    if not state:
        return None
    if state.get("status") not in {"APPROVED", "STALE"}:
        return state
    scene_index = int(state.get("scene_index") or 0)
    updated = upsert_scene_state(
        project_id, scene_id, scene_index, status="STALE", blocked_reason=reason, error=reason, force=True,
    )
    try:
        from .film_event_store import emit_event
        emit_event(project_id, "SNAPSHOT_STALE", scene_id=scene_id, severity="WARN", payload={"reason": reason})
    except Exception:
        pass
    mark_junctions_stale_for_scene(project_id, scene_id, reason)
    mark_final_stale(project_id, reason)
    return updated


def propagate_scene_change(project_id: str, scene_id: str, reason: str | None = None) -> dict:
    detail = reason or f"SCENE_STALE: {scene_id} source changed"
    scene = mark_scene_stale(project_id, scene_id, detail)
    return {"scene": scene, "reason": detail}


def propagate_canonical_change(project_id: str, resource_type: str | None = None, entity_id: str | None = None) -> list[dict]:
    changed = []
    token = f"{resource_type}:{entity_id}" if resource_type and entity_id else None
    for state in list_scene_states(project_id):
        if state.get("status") != "APPROVED":
            continue
        latest = latest_acceptance_snapshot(project_id, state["scene_id"])
        payload = (latest or {}).get("payload") or {}
        canon = payload.get("canonical") or []
        hit = False
        if token:
            hit = any(f"{item.get('resource_type')}:{item.get('entity_id')}" == token for item in canon)
        if not token or hit or snapshot_fingerprint_mismatch(project_id, state["scene_id"]):
            changed.append(propagate_scene_change(project_id, state["scene_id"], f"CANONICAL_STALE: {token or 'canonical changed'}"))
    return changed


def propagate_voice_change(project_id: str, character_id: str) -> list[dict]:
    changed = []
    cid = str(character_id)
    project = get_film_project(project_id) or {}
    for scene in project.get("scenes") or []:
        chars = {str(x) for x in (scene.get("characters") or [])}
        narrator_used = cid == "NARRATOR" and bool(str(scene.get("voiceover") or "").strip())
        if cid not in chars and not narrator_used:
            continue
        state = get_scene_state(project_id, scene["id"]) or {}
        if state.get("status") == "APPROVED":
            changed.append(propagate_scene_change(project_id, scene["id"], f"VOICE_STALE: {cid}"))
    return changed


def refresh_project_staleness(project_id: str) -> dict:
    stale_scenes = []
    for state in list_scene_states(project_id):
        if state.get("status") != "APPROVED":
            continue
        if snapshot_fingerprint_mismatch(project_id, state["scene_id"]):
            stale_scenes.append(propagate_scene_change(project_id, state["scene_id"], "SNAPSHOT_FINGERPRINT_MISMATCH"))
    return {"stale_scenes": stale_scenes}


def ensure_acceptance_snapshot(project_id: str, scene_id: str) -> dict | None:
    state = get_scene_state(project_id, scene_id) or {}
    if state.get("status") != "APPROVED":
        return None
    latest = latest_acceptance_snapshot(project_id, scene_id)
    if latest:
        payload = latest.get("payload") or {}
        fingerprint = current_fingerprint(project_id, scene_id)
        same_media = payload.get("selected_media_id") == fingerprint.get("selected_media_id")
        same_version = int(payload.get("selected_media_version") or 0) == int(fingerprint.get("selected_media_version") or 0)
        if same_media and same_version:
            return public_snapshot(latest)
    created = create_acceptance_snapshot(project_id, scene_id)
    try:
        from .film_event_store import emit_event
        emit_event(project_id, "SNAPSHOT_CREATED", scene_id=scene_id, payload={"snapshot_id": (created or {}).get("id"), "snapshot_hash": (created or {}).get("snapshot_hash")})
    except Exception:
        pass
    return created


def validate_approval_for_snapshot(project_id: str, scene_id: str) -> tuple[bool, str | None]:
    state = get_scene_state(project_id, scene_id) or {}
    if state.get("status") != "APPROVED":
        return False, "SCENE_NOT_APPROVED"
    media = _selected_video(project_id, scene_id, state)
    if not media or not media.get("is_selected"):
        return False, "SELECTED_MEDIA_REQUIRED"
    if media.get("status") != "completed" or media.get("qc_status") != "passed":
        return False, "SELECTED_MEDIA_INVALID"
    ledger = get_ledger(project_id, scene_id) or {}
    if not ledger:
        return False, "LEDGER_MISSING"
    if not ledger.get("accepted_first_frame") or not ledger.get("accepted_last_frame"):
        return False, "BOUNDARY_FRAMES_MISSING"
    return True, None


def backfill_acceptance_snapshots(project_id: str, scene_id: str | None = None) -> dict:
    created = []
    skipped = []
    blocked = []
    states = list_scene_states(project_id)
    if scene_id:
        states = [item for item in states if item.get("scene_id") == scene_id]
    for state in states:
        if state.get("status") != "APPROVED":
            continue
        sid = state["scene_id"]
        existing = list_acceptance_snapshots(project_id, sid)
        if existing:
            skipped.append({"scene_id": sid, "count": len(existing), "latest_id": existing[0].get("id")})
            continue
        ok, code = validate_approval_for_snapshot(project_id, sid)
        if not ok:
            blocked.append({"scene_id": sid, "code": "BACKFILL_BLOCKED", "reason": code})
            continue
        created.append(create_acceptance_snapshot(project_id, sid))
    try:
        if created:
            from .film_event_store import emit_event
            emit_event(project_id, "SNAPSHOT_BACKFILLED", payload={"created_count": len(created), "skipped_count": len(skipped)})
    except Exception:
        pass
    return {
        "project_id": project_id,
        "created": created,
        "skipped": skipped,
        "blocked": blocked,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "blocked_count": len(blocked),
    }


def get_public_acceptance_snapshot(project_id: str, snapshot_id: str) -> dict | None:
    return public_snapshot(get_acceptance_snapshot(project_id, snapshot_id))
