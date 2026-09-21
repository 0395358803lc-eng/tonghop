from __future__ import annotations

from .film_audio_schema import normalize_audio_requirements, speech_required
from .film_voice_profile_store import save_scene_audio_requirements
from .film_store import get_film_project


def scene_dialogue_requirements(project: dict | None, scene: dict) -> dict:
    req = normalize_audio_requirements(scene, project)
    scene_chars = {str(x) for x in (scene.get("characters") or [])}
    for item in req.get("dialogue") or []:
        cid = item.get("speaker_character_id")
        if not cid:
            item["error"] = item.get("error") or "DIALOGUE_SPEAKER_MISSING"
        elif scene_chars and cid not in scene_chars:
            item["error"] = "SPEAKER_NOT_IN_SCENE"
            req.setdefault("errors", []).append({"code": "SPEAKER_NOT_IN_SCENE", "detail": cid})
    return req


def project_audio_requirements(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = []
    for scene in project.get("scenes") or []:
        req = scene_dialogue_requirements(project, scene)
        save_scene_audio_requirements(project_id, scene.get("id"), req)
        scenes.append(req)
    return {
        "project_id": project_id,
        "scenes": scenes,
        "speech_required_count": sum(1 for item in scenes if item.get("speech_required")),
    }


def required_speaker_ids(scene: dict, project: dict | None = None) -> list[str]:
    req = scene_dialogue_requirements(project, scene)
    return list(req.get("visible_speakers") or [])


def dialogue_is_valid(scene: dict, project: dict | None = None) -> tuple[bool, list[dict]]:
    req = scene_dialogue_requirements(project, scene)
    errors = list(req.get("errors") or [])
    if speech_required(scene):
        texts = [item.get("text") for item in (req.get("dialogue") or []) if item.get("text")]
        vos = [item.get("text") for item in (req.get("voiceover") or []) if item.get("text")]
        if not texts and not vos:
            errors.append({"code": "DIALOGUE_TEXT_MISSING", "detail": scene.get("id")})
        for item in req.get("dialogue") or []:
            if item.get("visible_speaker_required") and not item.get("speaker_character_id"):
                errors.append({"code": "DIALOGUE_SPEAKER_MISSING", "detail": scene.get("id")})
    return not errors, errors
