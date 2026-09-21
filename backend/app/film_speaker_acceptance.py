from __future__ import annotations

from pathlib import Path

from .film_audio_schema import normalize_audio_requirements
from .film_media_store import get_selected_media
from .film_qc_service import _expected_speech_lines, _transcribe_speech_sync
from .film_speaker_calibration import calibrate_project_speakers
from .film_speaker_identity import reset_speaker_reference, verify_or_enroll_scene_speaker
from .film_store import get_film_project


def run_project_speaker_acceptance(project_id: str, recalibrate: bool = True) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    calibration = calibrate_project_speakers(project_id) if recalibrate else None
    if calibration:
        speaker_ids = sorted({
            str(item.get("speaker_character_id") or "").strip()
            for item in (calibration.get("samples") or [])
            if str(item.get("speaker_character_id") or "").strip()
        })
        for speaker_id in speaker_ids:
            reset_speaker_reference(project_id, speaker_id)

    items = []
    for scene in project.get("scenes") or []:
        expected = _expected_speech_lines(scene)
        if not expected:
            continue

        req = normalize_audio_requirements(scene, project)
        media = get_selected_media(project_id, f"scene_video:{scene['id']}")
        if not media or not media.get("file_path"):
            items.append({
                "scene_id": scene["id"],
                "status": "missing_media",
                "passed": False,
                "error": "SELECTED_SCENE_MEDIA_MISSING",
            })
            continue

        path = Path(str(media["file_path"]))
        if not path.is_file():
            items.append({
                "scene_id": scene["id"],
                "status": "missing_file",
                "passed": False,
                "error": "SELECTED_SCENE_FILE_MISSING",
            })
            continue

        speech = _transcribe_speech_sync(path, expected)
        identity = verify_or_enroll_scene_speaker(project, scene, path, speech)
        status = str(identity.get("status") or "not_evaluated")
        passed = status in {"enrolled_reference", "passed", "not_required"}
        items.append({
            "scene_id": scene["id"],
            "media_id": media.get("id"),
            "expected_speakers": req.get("speakers") or [],
            "speaker_character_id": identity.get("speaker_character_id"),
            "status": status,
            "passed": passed,
            "speaker_similarity": identity.get("speaker_similarity"),
            "speaker_similarity_percent": identity.get("speaker_similarity_percent"),
            "raw_similarity": identity.get("raw_similarity"),
            "threshold": identity.get("threshold"),
            "threshold_source": identity.get("threshold_source"),
            "calibrated": identity.get("calibrated"),
            "reference_scene_id": identity.get("reference_scene_id"),
            "reference_created": identity.get("reference_created"),
            "stt_passed": speech.get("passed"),
            "stt_match_score": speech.get("match_score"),
            "language": speech.get("language"),
            "speech_segments": len(speech.get("segments") or []),
            "word_count": sum(
                len(seg.get("words") or [])
                for seg in (speech.get("segments") or [])
                if isinstance(seg, dict)
            ),
            "error": identity.get("error"),
        })

    verified = [item for item in items if item.get("status") == "passed"]
    enrolled = [item for item in items if item.get("status") == "enrolled_reference"]
    failed = [item for item in items if item.get("status") == "failed"]
    blocked = [
        item for item in items
        if item.get("status") not in {"passed", "enrolled_reference", "not_required"}
    ]
    similarities = [
        float(item["speaker_similarity"])
        for item in verified
        if item.get("speaker_similarity") is not None
    ]

    return {
        "project_id": project_id,
        "calibration": {
            "status": (calibration or {}).get("status"),
            "character_status": (calibration or {}).get("character_status"),
            "narrator_status": (calibration or {}).get("narrator_status"),
            "model": (calibration or {}).get("model"),
            "embedding_strategy": (calibration or {}).get("embedding_strategy"),
            "threshold": (calibration or {}).get("threshold"),
            "positive_min": (calibration or {}).get("positive_min"),
            "negative_max": (calibration or {}).get("negative_max"),
            "gap": (calibration or {}).get("gap"),
            "sample_count": (calibration or {}).get("sample_count"),
            "speaker_count": (calibration or {}).get("speaker_count"),
            "speaker_calibrations": (calibration or {}).get("speaker_calibrations") or {},
            "skipped": (calibration or {}).get("skipped") or [],
        },
        "speech_scenes": len(items),
        "enrolled_references": len(enrolled),
        "verified_scenes": len(verified),
        "failed_scenes": len(failed),
        "blocked_scenes": len(blocked),
        "min_similarity": round(min(similarities), 6) if similarities else None,
        "max_similarity": round(max(similarities), 6) if similarities else None,
        "average_similarity": (
            round(sum(similarities) / len(similarities), 6) if similarities else None
        ),
        "passed": bool(items) and (calibration or {}).get("status") == "calibrated" and not failed and not blocked,
        "items": items,
    }
