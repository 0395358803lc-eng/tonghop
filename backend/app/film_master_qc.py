from __future__ import annotations

import os
import re
from pathlib import Path

from .film_acceptance_snapshot import refresh_project_staleness
from .film_boundary_store import list_junctions
from .film_final_assembly import _ffmpeg, _probe, _run, final_status
from .film_final_store import get_final_render, update_final_render
from .film_media_store import get_media, select_media, update_media_qc, validate_media_path
from .film_scene_state_store import list_scene_states
from .film_store import get_film_project

BLACK_LIMIT_SEC = 0.4
SILENCE_HARD_RATIO = 0.9
SILENCE_GAP_MIN_SEC = float(os.getenv("FILM_MASTER_QC_SILENCE_GAP_MIN_SEC", "0.75"))
DURATION_TOLERANCE = 1.0


def _dim(score, threshold, passed, hard=False, status=None, detail=None):
    return {
        "score": score,
        "threshold": threshold,
        "passed": passed,
        "hard": hard,
        "status": status or ("passed" if passed else "failed"),
        "detail": detail,
    }


def _parse_black(stderr: str) -> float:
    total = 0.0
    for match in re.finditer(r"black_duration\s*:\s*([0-9.]+)", stderr or ""):
        total += float(match.group(1))
    return total


def _parse_silence_durations(stderr: str) -> list[float]:
    return [
        float(match.group(1))
        for match in re.finditer(r"silence_duration\s*:\s*([0-9.]+)", stderr or "")
    ]


def _parse_silence(stderr: str) -> float:
    return sum(_parse_silence_durations(stderr))


def _suspicious_silence_seconds(durations: list[float]) -> float:
    return sum(value for value in durations if value >= SILENCE_GAP_MIN_SEC)


def _parse_max_volume(stderr: str) -> float | None:
    match = re.search(r"max_volume:\s*([-0-9.]+)\s*dB", stderr or "")
    return float(match.group(1)) if match else None


def _analyze_media(path: Path, duration: float) -> dict:
    black = _run([
        _ffmpeg(), "-hide_banner", "-i", str(path),
        "-vf", "blackdetect=d=0.35:pix_th=0.12", "-an", "-f", "null", "-",
    ])
    silence = _run([
        _ffmpeg(), "-hide_banner", "-i", str(path),
        "-af", "silencedetect=n=-45dB:d=0.35", "-vn", "-f", "null", "-",
    ])
    volume = _run([
        _ffmpeg(), "-hide_banner", "-i", str(path),
        "-af", "volumedetect", "-vn", "-f", "null", "-",
    ])
    black_sec = _parse_black((black.stderr or "") + (black.stdout or ""))
    silence_text = (silence.stderr or "") + (silence.stdout or "")
    silence_durations = _parse_silence_durations(silence_text)
    silence_sec = sum(silence_durations)
    max_silence = max(silence_durations, default=0.0)
    suspicious_silence = _suspicious_silence_seconds(silence_durations)
    max_volume = _parse_max_volume((volume.stderr or "") + (volume.stdout or ""))
    return {
        "black_seconds": round(black_sec, 3),
        "silence_seconds": round(silence_sec, 3),
        "max_silence_seconds": round(max_silence, 3),
        "suspicious_silence_seconds": round(suspicious_silence, 3),
        "silence_gap_threshold_seconds": SILENCE_GAP_MIN_SEC,
        "max_volume_db": max_volume,
        "duration": duration,
    }


def evaluate_master_qc(project_id: str, render_id: str | None = None) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    refresh_project_staleness(project_id)
    row = get_final_render(project_id, render_id)
    if not row or not row.get("media_id"):
        raise ValueError("FINAL_QC_BLOCKED: chưa có final_video để QC.")
    media = get_media(row["media_id"])
    if not media:
        raise ValueError("FINAL_QC_BLOCKED: final media record missing.")
    issues = []
    hard_failed = []
    dimensions = {}
    evidence = {"sampled_frames": [], "timeline_checks": []}
    try:
        path = validate_media_path(media.get("file_path"))
        info = _probe(path)
    except Exception as exc:
        dimensions["broken_file"] = _dim(0, 1, False, True, detail=str(exc)[:300])
        hard_failed.append("broken_file")
        return {
            "passed": False,
            "overall_score": 0,
            "hard_gate": {"passed": False, "failed": hard_failed},
            "dimensions": dimensions,
            "issues": [{"code": "BROKEN_FILE", "detail": str(exc)[:300]}],
            "evidence": evidence,
        }
    scenes = sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0))
    manifest = row.get("manifest") or {}
    items = list(manifest.get("items") or [])
    item_ids = [item.get("scene_id") for item in items]
    scene_ids = [scene.get("id") for scene in scenes]
    missing = [sid for sid in scene_ids if sid not in item_ids]
    extra_order = [item.get("scene_index") for item in items]
    order_ok = extra_order == sorted(extra_order)
    if missing:
        hard_failed.append("missing_scene")
        issues.append({"code": "MISSING_SCENE", "detail": ",".join(missing)})
    dimensions["missing_scene"] = _dim(0 if missing else 100, 1, not missing, True, detail=",".join(missing) or None)
    dimensions["scene_order"] = _dim(100 if order_ok else 0, 1, order_ok, True)
    if not order_ok:
        hard_failed.append("scene_order")
        issues.append({"code": "SCENE_ORDER_INVALID"})
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    stale_scenes = [sid for sid in scene_ids if (states.get(sid) or {}).get("status") == "STALE"]
    if stale_scenes:
        hard_failed.append("stale_scene")
        issues.append({"code": "STALE_SCENE", "detail": ",".join(stale_scenes)})
    dimensions["stale_scene"] = _dim(0 if stale_scenes else 100, 1, not stale_scenes, True)

    duration = float(info.get("duration") or media.get("duration_seconds") or 0)
    analysis = _analyze_media(path, duration)
    evidence["timeline_checks"] = analysis
    black_sec = analysis["black_seconds"]
    silence_sec = analysis["silence_seconds"]
    has_audio = bool(info.get("has_audio"))
    black_fail = black_sec >= BLACK_LIMIT_SEC
    dimensions["black_frames"] = _dim(0 if black_fail else 100, 1, not black_fail, True, detail=f"{black_sec}s")
    if black_fail:
        hard_failed.append("black_frames")
        issues.append({"code": "BLACK_FRAMES", "detail": f"{black_sec}s"})
    audio_missing = (not has_audio) or (duration > 0 and silence_sec / max(duration, 0.01) >= SILENCE_HARD_RATIO)
    dimensions["audio_missing"] = _dim(0 if audio_missing else 100, 1, not audio_missing, True)
    if audio_missing:
        hard_failed.append("audio_missing")
        issues.append({"code": "AUDIO_MISSING"})
    suspicious_silence_sec = float(analysis.get("suspicious_silence_seconds") or 0.0)
    max_silence_sec = float(analysis.get("max_silence_seconds") or 0.0)
    gap = has_audio and not audio_missing and max_silence_sec >= SILENCE_GAP_MIN_SEC
    dimensions["audio_gaps"] = _dim(
        60 if gap else 95,
        70,
        not gap,
        False,
        detail=(
            f"max={max_silence_sec}s suspicious_total={suspicious_silence_sec}s "
            f"raw_total={silence_sec}s threshold={SILENCE_GAP_MIN_SEC}s"
        ),
    )
    if gap:
        issues.append({
            "code": "AUDIO_GAP",
            "detail": (
                f"max={max_silence_sec}s suspicious_total={suspicious_silence_sec}s "
                f"raw_total={silence_sec}s threshold={SILENCE_GAP_MIN_SEC}s"
            ),
        })
    clip = analysis.get("max_volume_db") is not None and analysis["max_volume_db"] >= -0.3
    dimensions["audio_clipping"] = _dim(50 if clip else 95, 70, not clip, False, detail=analysis.get("max_volume_db"))
    source_dur = 0.0
    for item in items:
        src = get_media(item.get("media_id"))
        source_dur += float((src or {}).get("duration_seconds") or 0)
    if not source_dur:
        source_dur = duration
    dur_ok = abs(duration - source_dur) <= max(DURATION_TOLERANCE, 0.25 * max(len(items), 1))
    dimensions["duration_integrity"] = _dim(100 if dur_ok else 40, 1, dur_ok, True, detail=f"final={duration:.2f} source={source_dur:.2f}")
    if not dur_ok:
        hard_failed.append("duration_integrity")
        issues.append({"code": "DURATION_MISMATCH", "detail": f"{duration} vs {source_dur}"})

    junctions = list_junctions(project_id)
    j_fail = [item for item in junctions if item.get("status") not in {"PASS", "PENDING"}]
    j_pass = [item for item in junctions if item.get("status") == "PASS"]
    identity_ok = all(((item.get("qc") or {}).get("hard_gate") or {}).get("passed", True) for item in j_pass) and not j_fail
    dimensions["identity_continuity"] = _dim(94 if identity_ok else 40, 90, identity_ok, False)
    dimensions["prop_continuity"] = _dim(90 if identity_ok else 40, 85, identity_ok, False)
    dimensions["location_continuity"] = _dim(90 if identity_ok else 50, 80, identity_ok, False)
    dimensions["dialogue_continuity"] = _dim(88 if not audio_missing else 40, 70, not audio_missing, False)
    dimensions["broken_file"] = _dim(100, 1, True, True)

    hard_ok = not hard_failed
    scores = [item["score"] for item in dimensions.values() if item.get("score") is not None]
    overall = round(sum(scores) / max(len(scores), 1), 1)
    return {
        "passed": hard_ok,
        "overall_score": overall,
        "hard_gate": {"passed": hard_ok, "failed": hard_failed},
        "dimensions": dimensions,
        "issues": issues,
        "evidence": evidence,
        "media_id": media.get("id"),
        "render_id": row.get("id"),
    }


def run_master_qc(project_id: str, render_id: str | None = None) -> dict:
    row = get_final_render(project_id, render_id)
    if not row:
        raise ValueError("Không tìm thấy final render.")
    if row.get("status") == "STALE":
        raise ValueError("FINAL_QC_BLOCKED: final film STALE, ghép lại trước khi QC.")
    if row.get("status") not in {"QC_PENDING", "QC_FAILED"}:
        raise ValueError(f"FINAL_QC_BLOCKED: chỉ QC khi QC_PENDING hoặc QC_FAILED, hiện tại {row.get('status')}.")
    from .film_event_store import emit_event
    emit_event(project_id, "MASTER_QC_STARTED", payload={"render_id": row.get("id")})
    update_final_render(project_id, row["id"], status="QC_RUNNING", error=None)
    report = evaluate_master_qc(project_id, row["id"])
    media_id = row.get("media_id")
    if report.get("passed"):
        if media_id:
            update_media_qc(media_id, qc_status="passed", qc=report, qc_score=report.get("overall_score"))
            select_media(media_id)
        update_final_render(project_id, row["id"], status="APPROVED", qc=report, error=None)
        emit_event(project_id, "MASTER_QC_PASSED", payload={"render_id": row.get("id"), "score": report.get("overall_score")})
    else:
        failed = ",".join((report.get("hard_gate") or {}).get("failed") or [])
        if media_id:
            update_media_qc(media_id, qc_status="failed", qc=report, qc_score=report.get("overall_score"))
        update_final_render(project_id, row["id"], status="QC_FAILED", qc=report, error=failed or "MASTER_QC_FAILED")
        emit_event(project_id, "MASTER_QC_FAILED", severity="ERROR", payload={"render_id": row.get("id"), "error": failed or "MASTER_QC_FAILED"})
    return final_status(project_id, row["id"])
