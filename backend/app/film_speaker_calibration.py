from __future__ import annotations

from itertools import combinations

import numpy as np

from .film_audio_schema import normalize_audio_requirements
from .film_media_store import get_selected_media
from .film_qc_service import _expected_speech_lines, _transcribe_speech_sync
from .film_speaker_identity import (
    SPEAKER_CALIBRATION_MIN_GAP,
    SPEAKER_MODEL_PATH,
    SPEAKER_STRATEGY,
    cosine_similarity,
    save_project_calibration,
    speaker_embedding_from_video,
)
from .film_store import get_film_project


def build_speaker_calibration(samples: list[dict]) -> dict:
    positives: list[dict] = []
    negatives: list[dict] = []

    for left, right in combinations(samples, 2):
        a = np.asarray(left["embedding"], dtype=np.float32)
        b = np.asarray(right["embedding"], dtype=np.float32)
        similarity = cosine_similarity(a, b)
        row = {
            "scene_a": left["scene_id"],
            "scene_b": right["scene_id"],
            "speaker_a": left["speaker_character_id"],
            "speaker_b": right["speaker_character_id"],
            "similarity": round(similarity, 6),
        }
        if left["speaker_character_id"] == right["speaker_character_id"]:
            positives.append(row)
        else:
            negatives.append(row)

    speaker_ids = sorted({str(item["speaker_character_id"]) for item in samples})
    character_ids = [speaker_id for speaker_id in speaker_ids if speaker_id != "NARRATOR"]
    speaker_calibrations: dict[str, dict] = {}

    for speaker_id in speaker_ids:
        own_positive = [
            row for row in positives
            if row["speaker_a"] == speaker_id and row["speaker_b"] == speaker_id
        ]
        if speaker_id == "NARRATOR":
            own_negative = [
                row for row in negatives
                if speaker_id in {row["speaker_a"], row["speaker_b"]}
            ]
        else:
            own_negative = [
                row for row in negatives
                if speaker_id in {row["speaker_a"], row["speaker_b"]}
                and "NARRATOR" not in {row["speaker_a"], row["speaker_b"]}
            ]

        row = {
            "speaker_id": speaker_id,
            "positive_pair_count": len(own_positive),
            "negative_pair_count": len(own_negative),
            "min_required_gap": SPEAKER_CALIBRATION_MIN_GAP,
        }
        if not own_positive or not own_negative:
            row.update({
                "status": "insufficient_samples",
                "threshold": None,
                "gap": None,
                "reason": "Need same-speaker and cross-character samples for calibration.",
            })
            speaker_calibrations[speaker_id] = row
            continue

        positive_values = [float(item["similarity"]) for item in own_positive]
        negative_values = [float(item["similarity"]) for item in own_negative]
        positive_min = min(positive_values)
        negative_max = max(negative_values)
        gap = positive_min - negative_max
        row.update({
            "positive_min": round(positive_min, 6),
            "positive_avg": round(sum(positive_values) / len(positive_values), 6),
            "positive_max": round(max(positive_values), 6),
            "negative_min": round(min(negative_values), 6),
            "negative_avg": round(sum(negative_values) / len(negative_values), 6),
            "negative_max": round(negative_max, 6),
            "gap": round(gap, 6),
        })
        if gap >= SPEAKER_CALIBRATION_MIN_GAP:
            row.update({
                "status": "calibrated",
                "threshold": round((positive_min + negative_max) / 2.0, 6),
                "reason": "Speaker-specific midpoint between worst same-speaker and closest relevant negative pair.",
            })
        else:
            row.update({
                "status": "ambiguous",
                "threshold": None,
                "reason": "Speaker-specific positive/negative distributions overlap or margin is too small.",
            })
        speaker_calibrations[speaker_id] = row

    character_rows = [speaker_calibrations[speaker_id] for speaker_id in character_ids]
    characters_calibrated = bool(character_rows) and all(row.get("status") == "calibrated" for row in character_rows)
    narrator_row = speaker_calibrations.get("NARRATOR")
    narrator_calibrated = narrator_row is None or narrator_row.get("status") == "calibrated"

    if characters_calibrated and narrator_calibrated:
        overall_status = "calibrated"
    elif characters_calibrated:
        overall_status = "partial"
    elif any(row.get("status") == "ambiguous" for row in speaker_calibrations.values()):
        overall_status = "ambiguous"
    else:
        overall_status = "insufficient_samples"

    positive_values = [float(item["similarity"]) for item in positives]
    negative_values = [float(item["similarity"]) for item in negatives]
    global_stats = {
        "positive_min": round(min(positive_values), 6) if positive_values else None,
        "positive_avg": round(sum(positive_values) / len(positive_values), 6) if positive_values else None,
        "positive_max": round(max(positive_values), 6) if positive_values else None,
        "negative_min": round(min(negative_values), 6) if negative_values else None,
        "negative_avg": round(sum(negative_values) / len(negative_values), 6) if negative_values else None,
        "negative_max": round(max(negative_values), 6) if negative_values else None,
        "gap": (
            round(min(positive_values) - max(negative_values), 6)
            if positive_values and negative_values else None
        ),
    }

    return {
        "model": SPEAKER_MODEL_PATH.name,
        "embedding_strategy": SPEAKER_STRATEGY,
        "sample_count": len(samples),
        "speaker_count": len(speaker_ids),
        "positive_pairs": positives,
        "negative_pairs": negatives,
        "positive_pair_count": len(positives),
        "negative_pair_count": len(negatives),
        "min_required_gap": SPEAKER_CALIBRATION_MIN_GAP,
        "speaker_calibrations": speaker_calibrations,
        "character_status": "calibrated" if characters_calibrated else "not_calibrated",
        "narrator_status": (
            "not_present" if narrator_row is None else narrator_row.get("status")
        ),
        "status": overall_status,
        "threshold": None,
        **global_stats,
        "reason": "Per-speaker calibration; narrator is evaluated in a separate lane.",
    }


def calibrate_project_speakers(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    samples: list[dict] = []
    skipped: list[dict] = []

    for scene in project.get("scenes") or []:
        req = normalize_audio_requirements(scene, project)
        speakers = [str(x) for x in (req.get("speakers") or []) if str(x).strip()]
        if len(speakers) != 1:
            if req.get("speech_required"):
                skipped.append({
                    "scene_id": scene.get("id"),
                    "reason": "MULTI_OR_UNKNOWN_SPEAKER",
                    "speakers": speakers,
                })
            continue

        media = get_selected_media(project_id, f"scene_video:{scene['id']}")
        if not media or not media.get("file_path"):
            skipped.append({"scene_id": scene.get("id"), "reason": "SELECTED_SCENE_MEDIA_MISSING"})
            continue

        expected = _expected_speech_lines(scene)
        speech = _transcribe_speech_sync(media["file_path"], expected)
        if not speech.get("available") or not speech.get("passed"):
            skipped.append({
                "scene_id": scene.get("id"),
                "reason": "STT_NOT_VERIFIED",
                "match_score": speech.get("match_score"),
            })
            continue

        try:
            embedding = speaker_embedding_from_video(media["file_path"], speech.get("segments"))
        except Exception as exc:
            skipped.append({
                "scene_id": scene.get("id"),
                "reason": "EMBEDDING_FAILED",
                "error": str(exc)[:600],
            })
            continue

        samples.append({
            "scene_id": scene["id"],
            "speaker_character_id": speakers[0],
            "embedding": embedding,
            "media_id": media.get("id"),
            "stt_match_score": speech.get("match_score"),
        })

    report = build_speaker_calibration(samples)
    report["project_id"] = project_id
    report["skipped"] = skipped
    report["samples"] = [
        {
            "scene_id": item["scene_id"],
            "speaker_character_id": item["speaker_character_id"],
            "media_id": item.get("media_id"),
            "stt_match_score": item.get("stt_match_score"),
        }
        for item in samples
    ]
    return save_project_calibration(project_id, report)
