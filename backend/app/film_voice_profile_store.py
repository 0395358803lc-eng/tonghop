from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import connect
from .film_audio_schema import character_records


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
    data["profile"] = _loads(data.pop("profile_json", None), {})
    return data


def default_voice_profile(character: dict) -> dict:
    cid = str(character.get("id") or "").strip()
    gender = str(character.get("gender") or character.get("sex") or "").strip().lower()
    if gender in {"nữ", "nu", "female", "f"}:
        pitch, gender_norm = "medium-high", "female"
    elif gender in {"nam", "male", "m"}:
        pitch, gender_norm = "medium-low", "male"
    else:
        pitch, gender_norm = "medium", gender or "unspecified"
    return {
        "character_id": cid,
        "language": "vi",
        "gender": gender_norm,
        "style": str(character.get("personality") or character.get("style") or "natural"),
        "pitch": pitch,
        "tempo": "moderate",
        "emotion_baseline": "calm",
        "provider": "flow",
        "provider_voice_id": None,
        "name": character.get("name"),
    }


def default_narrator_profile() -> dict:
    return {
        "character_id": "NARRATOR",
        "voice_profile_id": "VOICE_NARRATOR",
        "language": "vi",
        "gender": "neutral",
        "style": "natural cinematic narration",
        "pitch": "medium",
        "tempo": "moderate",
        "emotion_baseline": "calm",
        "provider": "flow",
        "provider_voice_id": None,
        "name": "Narrator",
        "voice_identity": "same adult Vietnamese narrator; stable neutral timbre, medium pitch, moderate pace, calm intimate delivery",
    }


def list_voice_profiles(project_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM film_voice_profiles WHERE project_id=? ORDER BY character_id",
            (project_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def get_voice_profile(project_id: str, character_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM film_voice_profiles WHERE project_id=? AND character_id=?",
            (project_id, character_id),
        ).fetchone()
    return _row(row)


def upsert_voice_profile(project_id: str, character_id: str, profile: dict | None = None, provider: str | None = None, provider_voice_id: str | None = None) -> dict:
    current = get_voice_profile(project_id, character_id)
    payload = dict((current or {}).get("profile") or {})
    payload.update(profile or {})
    payload["character_id"] = character_id
    payload["voice_profile_id"] = payload.get("voice_profile_id") or f"VOICE_{character_id}"
    vid = (current or {}).get("id") or str(uuid.uuid4())
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_voice_profiles(id,project_id,character_id,profile_json,provider,provider_voice_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id, character_id) DO UPDATE SET
                 profile_json=excluded.profile_json,
                 provider=COALESCE(excluded.provider, film_voice_profiles.provider),
                 provider_voice_id=COALESCE(excluded.provider_voice_id, film_voice_profiles.provider_voice_id),
                 updated_at=excluded.updated_at""",
            (
                vid,
                project_id,
                character_id,
                json.dumps(payload, ensure_ascii=False),
                provider or payload.get("provider") or (current or {}).get("provider"),
                provider_voice_id or payload.get("provider_voice_id") or (current or {}).get("provider_voice_id"),
                (current or {}).get("created_at") or now,
                now,
            ),
        )
    row = get_voice_profile(project_id, character_id) or {}
    if current is not None:
        from .film_acceptance_snapshot import propagate_voice_change
        propagate_voice_change(project_id, character_id)
    return row


def ensure_voice_profiles(project: dict) -> list[dict]:
    project_id = project["id"]
    for character in character_records(project):
        cid = str(character.get("id"))
        if not get_voice_profile(project_id, cid):
            upsert_voice_profile(project_id, cid, default_voice_profile(character))
    has_voiceover = any(
        str(scene.get("voiceover") or "").strip()
        for scene in (project.get("scenes") or [])
        if isinstance(scene, dict)
    )
    if has_voiceover and not get_voice_profile(project_id, "NARRATOR"):
        upsert_voice_profile(project_id, "NARRATOR", default_narrator_profile())
    return list_voice_profiles(project_id)


def update_voice_acoustic_evidence(project_id: str, character_id: str, evidence: dict) -> dict:
    current = get_voice_profile(project_id, character_id)
    if not current:
        current = upsert_voice_profile(
            project_id,
            character_id,
            {"character_id": character_id, "voice_profile_id": f"VOICE_{character_id}"},
        )
    payload = dict((current or {}).get("profile") or {})
    payload["acoustic_identity"] = dict(evidence or {})
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE film_voice_profiles
               SET profile_json=?, updated_at=?
               WHERE project_id=? AND character_id=?""",
            (
                json.dumps(payload, ensure_ascii=False),
                now,
                project_id,
                character_id,
            ),
        )
    return get_voice_profile(project_id, character_id) or {}


def clear_voice_acoustic_evidence(project_id: str, character_id: str) -> dict | None:
    current = get_voice_profile(project_id, character_id)
    if not current:
        return None
    payload = dict(current.get("profile") or {})
    payload.pop("acoustic_identity", None)
    with connect() as conn:
        conn.execute(
            """UPDATE film_voice_profiles
               SET profile_json=?, updated_at=?
               WHERE project_id=? AND character_id=?""",
            (
                json.dumps(payload, ensure_ascii=False),
                _now(),
                project_id,
                character_id,
            ),
        )
    return get_voice_profile(project_id, character_id)


def save_scene_audio_requirements(project_id: str, scene_id: str, requirements: dict) -> dict:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_scene_audio_requirements(project_id,scene_id,requirements_json,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(project_id, scene_id) DO UPDATE SET
                 requirements_json=excluded.requirements_json, updated_at=excluded.updated_at""",
            (project_id, scene_id, json.dumps(requirements, ensure_ascii=False), now),
        )
    return requirements


def get_scene_audio_requirements(project_id: str, scene_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT requirements_json FROM film_scene_audio_requirements WHERE project_id=? AND scene_id=?",
            (project_id, scene_id),
        ).fetchone()
    if not row:
        return None
    return _loads(row["requirements_json"], {})
