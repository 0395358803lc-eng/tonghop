from __future__ import annotations

import os

from .film_audio_schema import normalize_audio_requirements, speech_required
from .film_voice_profile_store import get_voice_profile

QC_DIALOGUE_MIN = 80.0
QC_SPEAKER_MIN = 85.0
QC_VOICE_MIN = float(os.getenv("FILM_QC_VOICE_MIN", "60"))
QC_AMBIENT_MIN = 70.0


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _dim(score, threshold, hard, status=None, issue=None):
    if status == "not_required":
        return {"score": None, "threshold": threshold, "status": "not_required", "passed": None, "hard": hard, "issue": issue}
    if status == "not_evaluated" or score is None:
        return {"score": None, "threshold": threshold, "status": "not_evaluated", "passed": None, "hard": hard, "issue": issue}
    passed = score >= threshold
    return {
        "score": score,
        "threshold": threshold,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "hard": hard,
        "issue": issue,
    }


def _gender_token(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"nữ", "nu", "female", "f", "woman"}:
        return "female"
    if text in {"nam", "male", "m", "man"}:
        return "male"
    return text


def _voice_similarity_percent(speech: dict) -> float | None:
    candidates = [
        speech.get("speaker_similarity"),
        speech.get("voice_similarity"),
        speech.get("speaker_match_score"),
        speech.get("voice_match_score"),
    ]
    verification = speech.get("speaker_verification")
    if isinstance(verification, dict):
        candidates.extend([
            verification.get("similarity"),
            verification.get("score"),
            verification.get("match_score"),
        ])
    for value in candidates:
        score = _num(value)
        if score is None:
            continue
        if 0.0 <= score <= 1.0:
            score *= 100.0
        return max(0.0, min(100.0, score))
    return None


def _voice_threshold_percent(speech: dict) -> float:
    verification = speech.get("speaker_verification")
    candidates = [speech.get("speaker_threshold")]
    if isinstance(verification, dict):
        candidates.insert(0, verification.get("threshold"))
    for value in candidates:
        score = _num(value)
        if score is None:
            continue
        if 0.0 < score <= 1.0:
            score *= 100.0
        if 0.0 < score <= 100.0:
            return score
    return QC_VOICE_MIN


def evaluate_audio_qc(scene: dict, qc: dict | None = None, project: dict | None = None, requirements: dict | None = None) -> dict:
    req = requirements or normalize_audio_requirements(scene, project)
    required = bool(req.get("speech_required") if req.get("speech_required") is not None else speech_required(scene))
    audio = (qc or {}).get("audio_check") if isinstance((qc or {}).get("audio_check"), dict) else {}
    speech = (qc or {}).get("speech_check") if isinstance((qc or {}).get("speech_check"), dict) else {}
    issues = []
    dims = {}

    if not required:
        dims["dialogue_presence"] = _dim(None, QC_DIALOGUE_MIN, True, status="not_required")
        dims["speaker_correctness"] = _dim(None, QC_SPEAKER_MIN, True, status="not_required")
        dims["speech"] = _dim(None, QC_SPEAKER_MIN, False, status="not_required")
    else:
        texts = [item.get("text") for item in (req.get("dialogue") or []) if item.get("text")]
        vos = [item.get("text") for item in (req.get("voiceover") or []) if item.get("text")]
        has_text = bool(texts or vos)
        present = bool(audio.get("present") and audio.get("non_silent")) or bool(speech.get("passed") is True) or bool(str(speech.get("transcript") or "").strip())
        if not has_text:
            dims["dialogue_presence"] = _dim(0.0, QC_DIALOGUE_MIN, True, issue="DIALOGUE_TEXT_MISSING")
            issues.append({"type": "dialogue_presence", "severity": "critical", "evidence": "Scene yêu cầu thoại nhưng thiếu nội dung."})
        elif present:
            dims["dialogue_presence"] = _dim(92.0, QC_DIALOGUE_MIN, True)
        else:
            dims["dialogue_presence"] = _dim(0.0, QC_DIALOGUE_MIN, True, issue="DIALOGUE_AUDIO_MISSING")
            issues.append({"type": "dialogue_presence", "severity": "critical", "evidence": "Scene có thoại nhưng audio/speech không có."})

        speaker_ok = True
        speaker_issue = None
        for item in req.get("dialogue") or []:
            cid = item.get("speaker_character_id")
            if item.get("error") or not cid:
                speaker_ok = False
                speaker_issue = item.get("error") or "DIALOGUE_SPEAKER_MISSING"
                break
            if cid not in [str(x) for x in (scene.get("characters") or [])] and scene.get("characters"):
                speaker_ok = False
                speaker_issue = "SPEAKER_NOT_IN_SCENE"
                break
        observed = speech.get("speaker_character_id") or speech.get("speaker_id") or speech.get("speaker")
        if speaker_ok and observed:
            expected = (req.get("speakers") or [None])[0]
            if expected and str(observed) != str(expected) and str(observed).upper() != str(expected).upper():
                speaker_ok = False
                speaker_issue = "SPEAKER_MISMATCH"
        dims["speaker_correctness"] = _dim(94.0 if speaker_ok else 20.0, QC_SPEAKER_MIN, True, issue=speaker_issue)
        if not speaker_ok:
            issues.append({"type": "speaker_correctness", "severity": "critical", "evidence": speaker_issue})

    drift = False
    drift_issue = None
    project_id = (project or {}).get("id") or scene.get("project_id")
    for cid in req.get("speakers") or []:
        profile_row = get_voice_profile(project_id, cid) if project_id else None
        profile = (profile_row or {}).get("profile") or {}
        observed_gender = _gender_token(speech.get("gender") or speech.get("voice_gender") or "")
        expected_gender = _gender_token(profile.get("gender") or "")
        if observed_gender and expected_gender and observed_gender != expected_gender:
            drift = True
            drift_issue = "VOICE_DRIFT"
            break
    voice_similarity = _voice_similarity_percent(speech)
    voice_threshold = _voice_threshold_percent(speech)
    if drift:
        dims["voice_continuity"] = _dim(15.0, voice_threshold, True, issue=drift_issue)
        issues.append({"type": "voice_continuity", "severity": "critical", "evidence": "Voice drift so với voice profile."})
    elif req.get("speakers") and voice_similarity is not None:
        dims["voice_continuity"] = _dim(
            voice_similarity,
            voice_threshold,
            True,
            issue=None if voice_similarity >= voice_threshold else "VOICE_SIMILARITY_LOW",
        )
        if voice_similarity < voice_threshold:
            issues.append({
                "type": "voice_continuity",
                "severity": "critical",
                "evidence": f"Acoustic speaker similarity {voice_similarity:.1f} < {voice_threshold:.1f}.",
            })
    elif req.get("speakers"):
        dims["voice_continuity"] = _dim(
            None,
            voice_threshold,
            False,
            status="not_evaluated",
            issue="ACOUSTIC_SPEAKER_EVIDENCE_MISSING",
        )
        issues.append({
            "type": "voice_continuity",
            "severity": "warning",
            "evidence": "Chưa có speaker/voice similarity thật; không tự gán điểm continuity.",
        })
    else:
        dims["voice_continuity"] = _dim(None, voice_threshold, False, status="not_required")

    if req.get("ambient"):
        dims["ambient_continuity"] = _dim(88.0 if audio.get("present") is not False else 40.0, QC_AMBIENT_MIN, False)
    else:
        dims["ambient_continuity"] = _dim(None, QC_AMBIENT_MIN, False, status="not_required")
    dims["sfx_presence"] = _dim(None, 70.0, False, status="not_required" if not req.get("sfx") else "not_evaluated")
    dims["music_continuity"] = _dim(None, 70.0, False, status="not_required" if not req.get("music") else "not_evaluated")
    clipping = audio.get("clipping") is True or (isinstance(audio.get("max_volume_db"), (int, float)) and audio.get("max_volume_db") >= -0.2)
    dims["audio_clipping"] = _dim(40.0 if clipping else 90.0, 70.0, False) if audio else _dim(None, 70.0, False, status="not_evaluated")
    gap = audio.get("gap") is True or audio.get("has_gap") is True
    dims["audio_gap"] = _dim(35.0 if gap else 90.0, 70.0, False) if audio else _dim(None, 70.0, False, status="not_evaluated")

    hard_failed = [name for name, item in dims.items() if item.get("hard") and item.get("passed") is False]
    return {
        "required": required,
        "speech_status": "required" if required else "not_required",
        "dimensions": dims,
        "hard_failed": hard_failed,
        "issues": issues,
        "requirements": {"speech_required": required, "speakers": req.get("speakers"), "errors": req.get("errors")},
    }


def merge_audio_qc(report: dict, scene: dict, qc: dict | None = None, project: dict | None = None) -> dict:
    audio = evaluate_audio_qc(scene, qc, project)
    dims = dict(report.get("dimensions") or {})
    incomplete = list(report.get("incomplete_dimensions") or [])
    hard_failed = list((report.get("hard_gate") or {}).get("failed") or [])
    hard_dims = dict((report.get("hard_gate") or {}).get("dimensions") or {})
    for name, item in (audio.get("dimensions") or {}).items():
        if item.get("status") == "not_required":
            current = dict(dims.get(name) or {})
            current.update(item)
            current["hard"] = False
            current["passed"] = None
            current["status"] = "not_required"
            dims[name] = current
            incomplete = [x for x in incomplete if x != name]
            hard_failed = [x for x in hard_failed if x != name]
            hard_dims.pop(name, None)
            continue
        if item.get("hard") and item.get("passed") is False:
            dims[name] = item
            hard_dims[name] = item
            if name not in hard_failed:
                hard_failed.append(name)
        elif item.get("hard") and item.get("status") == "not_evaluated":
            dims.setdefault(name, item)
            hard_dims.setdefault(name, item)
            if name not in incomplete:
                incomplete.append(name)
            if name not in hard_failed:
                hard_failed.append(name)
        elif name not in dims:
            dims[name] = item
    report["dimensions"] = dims
    report["incomplete_dimensions"] = incomplete
    report["qc_complete"] = not incomplete
    report["hard_gate"] = dict(report.get("hard_gate") or {})
    report["hard_gate"]["failed"] = hard_failed
    report["hard_gate"]["passed"] = not hard_failed
    report["hard_gate"]["dimensions"] = hard_dims
    report["dimensions_passed"] = sum(1 for item in dims.values() if item.get("passed") is True)
    report["dimensions_total"] = len(dims)
    report["issues"] = list(report.get("issues") or []) + list(audio.get("issues") or [])
    report["audio_requirements"] = audio.get("requirements")
    if hard_failed:
        report["passed"] = False
        if report.get("qc_status") != "error":
            report["qc_status"] = "failed"
    return report
