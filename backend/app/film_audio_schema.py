from __future__ import annotations

import re

CHAR_ID_RE = re.compile(r"^CHAR_\d+$", re.I)
SPEAKER_KEYS = ("speaker_character_id", "character_id", "speaker_id", "speaker")


def _text(value) -> str:
    return str(value or "").strip()


def character_records(project: dict | None) -> list[dict]:
    project = project or {}
    rows = project.get("characters") or project.get("character_bible") or []
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def character_lookup(project: dict | None) -> tuple[dict[str, dict], dict[str, str]]:
    by_id: dict[str, dict] = {}
    by_name: dict[str, str] = {}
    for row in character_records(project):
        cid = str(row.get("id") or "").strip()
        if not cid:
            continue
        by_id[cid] = row
        by_id[cid.upper()] = row
        name = _text(row.get("name")).lower()
        if name:
            by_name[name] = cid
        by_name[cid.lower()] = cid
    return by_id, by_name


def resolve_speaker_character_id(raw, project: dict | None, scene: dict | None = None) -> tuple[str | None, str | None]:
    by_id, by_name = character_lookup(project)
    scene_chars = [str(x) for x in ((scene or {}).get("characters") or [])]
    value = raw
    if isinstance(raw, dict):
        value = next((raw.get(key) for key in SPEAKER_KEYS if raw.get(key)), None)
    token = _text(value)
    if not token:
        return None, "DIALOGUE_SPEAKER_MISSING"
    if token in by_id:
        return by_id[token]["id"], None
    upper = token.upper()
    if CHAR_ID_RE.match(upper) and (not by_id or upper in by_id or upper in scene_chars):
        return upper, None
    mapped = by_name.get(token.lower())
    if mapped:
        return mapped, None
    return None, "UNKNOWN_SPEAKER"


def normalize_dialogue_line(raw, project: dict | None = None, scene: dict | None = None) -> dict:
    if isinstance(raw, str):
        payload = {"text": raw.strip()}
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = {}
    text = _text(payload.get("text") or payload.get("line") or payload.get("dialogue") or payload.get("content"))
    speaker_id, error = resolve_speaker_character_id(payload, project, scene)
    timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else {}
    start = timing.get("start", payload.get("start"))
    end = timing.get("end", payload.get("end"))
    visible = payload.get("visible_speaker_required")
    if visible is None:
        visible = True
    item = {
        "speaker_character_id": speaker_id,
        "text": text,
        "delivery": _text(payload.get("delivery") or payload.get("emotion")) or "calm",
        "language": _text(payload.get("language")) or "vi",
        "timing": {"start": start, "end": end},
        "visible_speaker_required": bool(visible),
        "voice_profile_id": _text(payload.get("voice_profile_id")) or (f"VOICE_{speaker_id}" if speaker_id else None),
    }
    if error:
        item["error"] = error
    return item


def normalize_voiceover(raw) -> list[dict]:
    text = _text(raw)
    if not text:
        return []
    return [{"text": text, "language": "vi", "type": "voiceover"}]


def speech_required(scene: dict | None) -> bool:
    scene = scene or {}
    if _text(scene.get("voiceover")):
        return True
    for item in scene.get("dialogue") or []:
        if isinstance(item, str) and item.strip():
            return True
        if isinstance(item, dict):
            if _text(item.get("text") or item.get("line") or item.get("dialogue") or item.get("content")):
                return True
            if any(item.get(key) for key in SPEAKER_KEYS):
                return True
    return False


def normalize_audio_requirements(scene: dict, project: dict | None = None) -> dict:
    dialogue = [normalize_dialogue_line(item, project, scene) for item in (scene.get("dialogue") or [])]
    voiceover = normalize_voiceover(scene.get("voiceover"))
    location_id = scene.get("location_id")
    atmosphere = _text(scene.get("atmosphere") or scene.get("time_of_day") or "DAY").upper().replace(" ", "_")
    ambient = []
    if location_id:
        ambient.append({
            "type": "location",
            "description": _text(scene.get("atmosphere") or "ambient"),
            "continuity_group": f"{location_id}_{atmosphere}",
        })
    continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
    sfx = list(continuity.get("sfx") or scene.get("sfx") or [])
    music = list(continuity.get("music") or scene.get("music") or [])
    required = speech_required(scene) or bool(dialogue) or bool(voiceover)
    speakers = []
    errors = []
    for item in dialogue:
        cid = item.get("speaker_character_id")
        if item.get("error"):
            errors.append({"code": item["error"], "detail": item.get("text") or "dialogue line"})
        if cid and cid not in speakers:
            speakers.append(cid)
    if voiceover and not speakers:
        speakers.append("NARRATOR")
    visible = [item["speaker_character_id"] for item in dialogue if item.get("visible_speaker_required") and item.get("speaker_character_id")]
    return {
        "scene_id": scene.get("id"),
        "speech_required": required,
        "speech_status": "required" if required else "not_required",
        "dialogue": dialogue,
        "voiceover": voiceover,
        "ambient": ambient,
        "sfx": sfx if isinstance(sfx, list) else [],
        "music": music if isinstance(music, list) else [],
        "speakers": speakers,
        "visible_speakers": visible,
        "voice_profile_ids": [f"VOICE_{cid}" for cid in speakers],
        "errors": errors,
        "language": "vi",
    }
