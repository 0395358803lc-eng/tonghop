from __future__ import annotations

from .film_audio_qc import merge_audio_qc
from .film_audio_schema import speech_required
from .film_qc_service import (
    QC_BOUNDARY_MIN,
    QC_IDENTITY_MIN,
    QC_LOCATION_MIN,
    QC_MIN_SCORE,
    QC_PROP_MIN,
    QC_WARDROBE_MIN,
    run_render_qc,
)

QC_V2_VERSION = "video-qc-v2"
QC_CAMERA_MIN = 70.0
QC_LIGHTING_MIN = 70.0
QC_AUDIO_MIN = 75.0
QC_SPEECH_MIN = 72.0

DIMENSIONS = (
    "identity",
    "wardrobe",
    "location",
    "prop",
    "boundary",
    "camera",
    "lighting",
    "audio",
    "speech",
)


def _num(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def required_hard_gates(scene: dict, job: dict | None = None) -> dict[str, float]:
    gates: dict[str, float] = {}
    if scene.get("characters"):
        gates["identity"] = QC_IDENTITY_MIN
    if scene.get("props_present"):
        gates["prop"] = QC_PROP_MIN
    if (job or {}).get("reference", {}).get("previous_scene_id") or (job or {}).get("previous_scene_id"):
        gates["boundary"] = QC_BOUNDARY_MIN
    if speech_required(scene) or scene.get("dialogue") or str(scene.get("voiceover") or "").strip():
        gates["speech"] = QC_SPEECH_MIN
    return gates


def _soft_thresholds(scene: dict, job: dict | None = None) -> dict[str, float]:
    thresholds = {
        "wardrobe": QC_WARDROBE_MIN if scene.get("characters") else None,
        "location": QC_LOCATION_MIN if scene.get("location_id") else None,
        "camera": QC_CAMERA_MIN,
        "lighting": QC_LIGHTING_MIN,
        "audio": QC_AUDIO_MIN if required_hard_gates(scene, job).get("speech") else None,
    }
    return {k: v for k, v in thresholds.items() if v is not None}


def _collect_scores(qc: dict) -> dict[str, float | None]:
    raw = qc.get("dimension_scores") if isinstance(qc.get("dimension_scores"), dict) else {}
    scores = {name: _num(raw.get(name)) for name in DIMENSIONS}
    hard = qc.get("hard_gate") if isinstance(qc.get("hard_gate"), dict) else {}
    hard_dims = hard.get("dimensions") if isinstance(hard.get("dimensions"), dict) else {}
    for name, item in hard_dims.items():
        if scores.get(name) is None and isinstance(item, dict):
            scores[name] = _num(item.get("score"))
    audio = qc.get("audio_check") if isinstance(qc.get("audio_check"), dict) else {}
    speech = qc.get("speech_check") if isinstance(qc.get("speech_check"), dict) else {}
    if scores.get("audio") is None:
        if audio.get("present") and audio.get("non_silent"):
            scores["audio"] = 90.0
        elif audio.get("required"):
            scores["audio"] = 20.0 if audio.get("present") else 0.0
    if scores.get("speech") is None and speech:
        if speech.get("passed") is True:
            scores["speech"] = max(QC_SPEECH_MIN, 100.0 * float(speech.get("match") or speech.get("ratio") or 0.9))
        elif speech.get("available") is False:
            scores["speech"] = None
        else:
            scores["speech"] = 100.0 * float(speech.get("match") or speech.get("ratio") or 0)
    return scores


def evaluate_qc_v2(qc_result: dict, scene: dict, job: dict | None = None, project: dict | None = None) -> dict:
    qc = dict(qc_result.get("qc") or {})
    scores = _collect_scores(qc)
    qc["dimension_scores"] = {k: v for k, v in scores.items() if v is not None}
    overall = _num(qc_result.get("consistency_score") if qc_result.get("consistency_score") is not None else qc.get("consistency_score"))
    gates = required_hard_gates(scene, job)
    soft = _soft_thresholds(scene, job)
    hard_results = {}
    hard_failed = []
    incomplete = []
    for name, threshold in gates.items():
        score = scores.get(name)
        if score is None:
            hard_results[name] = {"score": None, "threshold": threshold, "status": "not_evaluated", "passed": None, "hard": True}
            hard_failed.append(name)
            incomplete.append(name)
        else:
            passed_dim = score >= threshold
            hard_results[name] = {"score": score, "threshold": threshold, "status": "passed" if passed_dim else "failed", "passed": passed_dim, "hard": True}
            if not passed_dim:
                hard_failed.append(name)
    dim_results = dict(hard_results)
    for name, threshold in soft.items():
        if name in dim_results:
            continue
        score = scores.get(name)
        if score is None:
            dim_results[name] = {"score": None, "threshold": threshold, "status": "not_evaluated", "passed": None, "hard": False}
            incomplete.append(name)
        else:
            passed_dim = score >= threshold
            dim_results[name] = {"score": score, "threshold": threshold, "status": "passed" if passed_dim else "failed", "passed": passed_dim, "hard": False}
    identity_failed = "identity" in hard_failed
    passed_count = sum(1 for item in dim_results.values() if item.get("passed") is True)
    hard_ok = not hard_failed
    original_overall = overall
    original_passed_flag = bool(qc.get("passed")) if isinstance(qc.get("passed"), bool) else None
    overall_ok = overall is not None and overall >= QC_MIN_SCORE
    # Soft not_evaluated (camera/lighting) stays visible in incomplete/qc_complete=False
    # but must not fail overall. Hard not_evaluated already fails via hard_failed.
    passed = hard_ok and overall_ok
    if identity_failed:
        passed = False
    evidence = {
        "canonical": ((qc.get("visual_reference_evidence") or {}).get("canonical") if isinstance(qc.get("visual_reference_evidence"), dict) else []),
        "previous_accepted_last_frame": (job or {}).get("reference", {}).get("previous_last_frame_url") if job else None,
        "current_first_frame": (job or {}).get("first_frame_url"),
        "current_last_frame": (job or {}).get("last_frame_url"),
        "sampled_video_frames": qc.get("keyframes") or [],
        "audio": qc.get("audio_check"),
        "speech": qc.get("speech_check"),
    }
    report = {
        "version": QC_V2_VERSION,
        "qc_status": "passed" if passed else "failed" if qc_result.get("qc_status") != "error" else "error",
        "passed": passed,
        "consistency_score": original_overall,
        "identity_independent_fail": identity_failed and (
            (original_overall is not None and original_overall >= QC_MIN_SCORE) or original_passed_flag is True
        ),
        "hard_gate": {
            "version": QC_V2_VERSION,
            "passed": hard_ok,
            "failed": hard_failed,
            "dimensions": hard_results,
        },
        "dimensions": dim_results,
        "dimensions_passed": passed_count,
        "dimensions_total": len(dim_results),
        "incomplete_dimensions": incomplete,
        "qc_complete": not incomplete,
        "evidence": evidence,
        "issues": qc.get("issues") or [],
        "provider": qc.get("provider"),
        "model": qc.get("model"),
        "raw": qc,
    }
    report = merge_audio_qc(report, scene, qc, project)
    return {
        "qc_status": report["qc_status"],
        "consistency_score": original_overall,
        "qc": report,
    }


async def run_render_qc_v2(job: dict, project: dict, scene: dict) -> dict:
    base = await run_render_qc(job, project, scene)
    if base.get("qc_status") == "error" or base.get("qc_status") == "not_configured":
        return base
    return evaluate_qc_v2(base, scene, job, project)
