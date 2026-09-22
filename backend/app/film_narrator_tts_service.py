from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
from pathlib import Path

import httpx
import numpy as np

from .config import DATA_DIR
from .film_audio_schema import normalize_audio_requirements
from .film_media_store import get_selected_media, output_key_for
from .film_qc_service import _audio_metrics, _transcribe_speech_sync
from .film_speaker_identity import SPEAKER_THRESHOLD, cosine_similarity, speaker_embedding_from_video
from .film_store import get_film_project
from .provider_store import get_provider

TTS_PROVIDER = "xkiro"
TTS_MODEL = "xkiro-voice"
DEFAULT_NARRATOR_VOICE = "confident-male-vietnamese"
SUPPORTED_AUDIO_FORMAT = "mp3"
NARRATOR_TTS_VERSION = "narrator-tts-overlay-v1"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "item"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TTS_FFPROBE_FAILED: {(proc.stderr or '')[-500:]}")
    try:
        return float((proc.stdout or "").strip())
    except Exception as exc:
        raise RuntimeError("TTS_DURATION_INVALID") from exc


def video_stream_hash(path: str | Path) -> str:
    src = Path(path)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TTS_VIDEO_HASH_FAILED: {(proc.stderr or '')[-500:]}")
    line = (proc.stdout or "").strip()
    return line.split("=", 1)[-1] if "=" in line else line


def atempo_chain(factor: float) -> str:
    value = max(0.25, min(4.0, float(factor)))
    parts: list[str] = []
    while value > 2.0:
        parts.append("atempo=2.0")
        value /= 2.0
    while value < 0.5:
        parts.append("atempo=0.5")
        value /= 0.5
    parts.append(f"atempo={value:.6f}")
    return ",".join(parts)


def speech_windows_from_stt(stt: dict, total_duration: float, pad_seconds: float = 0.18) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    total = max(0.0, float(total_duration or 0.0))
    for segment in stt.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        words = segment.get("words") if isinstance(segment.get("words"), list) else []
        if words:
            starts = [float(item.get("start") or 0.0) for item in words if isinstance(item, dict)]
            ends = [float(item.get("end") or 0.0) for item in words if isinstance(item, dict)]
            if not starts or not ends:
                continue
            a, b = min(starts), max(ends)
        else:
            a = float(segment.get("start") or 0.0)
            b = float(segment.get("end") or a)
        if b <= a:
            continue
        spans.append([
            max(0.0, a - float(pad_seconds)),
            min(total, b + float(pad_seconds)),
        ])
    if not spans:
        return []
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if not merged or a > merged[-1][1] + 0.12:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def _project_dir(project_id: str) -> Path:
    folder = DATA_DIR / "narrator_tts" / _safe_id(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _tts_cache_path(project_id: str, voice_id: str, text: str, speed: float) -> Path:
    digest = _sha256_text(json.dumps(
        {
            "provider": TTS_PROVIDER,
            "model": TTS_MODEL,
            "voice": voice_id,
            "text": text,
            "speed": round(float(speed), 6),
            "format": SUPPORTED_AUDIO_FORMAT,
        },
        ensure_ascii=False,
        sort_keys=True,
    ))
    folder = _project_dir(project_id) / "tts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.mp3"


def _composite_path(project_id: str, scene_id: str, media: dict, voice_id: str, text: str) -> Path:
    digest = _sha256_text(json.dumps(
        {
            "version": NARRATOR_TTS_VERSION,
            "scene_id": scene_id,
            "base_media_id": media.get("id"),
            "base_media_version": media.get("version"),
            "voice": voice_id,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
    ))[:20]
    folder = _project_dir(project_id) / "composite"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{_safe_id(scene_id)}__{digest}.mp4"


def synthesize_narrator_tts(
    project_id: str,
    text: str,
    *,
    voice_id: str = DEFAULT_NARRATOR_VOICE,
    speed: float = 1.0,
) -> dict:
    body = str(text or "").strip()
    if not body:
        raise ValueError("NARRATOR_TTS_TEXT_REQUIRED")
    provider = get_provider(TTS_PROVIDER) or {}
    api_key = provider.get("api_key")
    if not api_key:
        raise ValueError("NARRATOR_TTS_PROVIDER_NOT_CONFIGURED")
    base_url = (provider.get("base_url") or "https://api.xkiro.com/v1").rstrip("/")
    dest = _tts_cache_path(project_id, voice_id, body, speed)
    cached = dest.is_file() and dest.stat().st_size > 512
    if not cached:
        response = httpx.post(
            f"{base_url}/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": TTS_MODEL,
                "input": body,
                "voice": voice_id,
                "response_format": SUPPORTED_AUDIO_FORMAT,
                "speed": float(speed),
            },
            timeout=90.0,
        )
        if response.status_code >= 400:
            detail = response.text[:1200]
            raise RuntimeError(f"NARRATOR_TTS_PROVIDER_ERROR:{response.status_code}:{detail}")
        dest.write_bytes(response.content)
    return {
        "provider": TTS_PROVIDER,
        "model": TTS_MODEL,
        "voice_id": voice_id,
        "path": str(dest),
        "cached": cached,
        "file_size": dest.stat().st_size,
        "duration": round(_ffprobe_duration(dest), 6),
        "cache_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
    }


def _narrator_scene(project: dict, scene_id: str) -> dict:
    scene = next((row for row in (project.get("scenes") or []) if row.get("id") == scene_id), None)
    if not scene:
        raise ValueError(f"NARRATOR_SCENE_NOT_FOUND:{scene_id}")
    req = normalize_audio_requirements(scene, project)
    speakers = [str(x) for x in (req.get("speakers") or []) if str(x).strip()]
    if speakers != ["NARRATOR"]:
        raise ValueError(f"NARRATOR_SCENE_REQUIRES_SINGLE_NARRATOR:{scene_id}:{speakers}")
    voiceover = str(scene.get("voiceover") or "").strip()
    if not voiceover:
        raise ValueError(f"NARRATOR_VOICEOVER_REQUIRED:{scene_id}")
    return scene


def compose_narrator_audio(
    project_id: str,
    scene_id: str,
    *,
    voice_id: str = DEFAULT_NARRATOR_VOICE,
    speed: float = 1.0,
) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scene = _narrator_scene(project, scene_id)
    text = str(scene.get("voiceover") or "").strip()
    media = get_selected_media(project_id, output_key_for(role="scene_video", scene_id=scene_id))
    if not media or not media.get("file_path"):
        raise ValueError(f"NARRATOR_SELECTED_MEDIA_REQUIRED:{scene_id}")
    src = Path(str(media["file_path"]))
    if not src.is_file():
        raise ValueError(f"NARRATOR_SELECTED_MEDIA_FILE_MISSING:{scene_id}")

    tts = synthesize_narrator_tts(project_id, text, voice_id=voice_id, speed=speed)
    tts_path = Path(str(tts["path"]))
    dest = _composite_path(project_id, scene_id, media, voice_id, text)

    total_duration = _ffprobe_duration(src)
    original_stt = _transcribe_speech_sync(src, [text])
    if original_stt.get("passed") is not True:
        raise RuntimeError(f"NARRATOR_BASE_STT_NOT_VERIFIED:{scene_id}")
    windows = speech_windows_from_stt(original_stt, total_duration)
    if not windows:
        raise RuntimeError(f"NARRATOR_SPEECH_WINDOW_MISSING:{scene_id}")

    start = windows[0][0]
    end = windows[-1][1]
    target_duration = max(0.8, end - start)
    tts_duration = float(tts["duration"])
    factor = tts_duration / target_duration
    mute_filters = ",".join(
        f"volume=0:enable='between(t,{a:.3f},{b:.3f})'"
        for a, b in windows
    )
    delay_ms = max(0, int(round(start * 1000)))
    filter_complex = (
        f"[0:a]{mute_filters}[amb];"
        f"[1:a]{atempo_chain(factor)},adelay={delay_ms}|{delay_ms},volume=1.0[tts];"
        f"[amb][tts]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.95[aout]"
    )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-i",
            str(tts_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-t",
            f"{total_duration:.6f}",
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"NARRATOR_TTS_COMPOSITE_FAILED:{(proc.stderr or '')[-1200:]}")

    source_hash = video_stream_hash(src)
    output_hash = video_stream_hash(dest)
    if source_hash != output_hash:
        raise RuntimeError(f"NARRATOR_VIDEO_STREAM_CHANGED:{scene_id}")

    new_stt = _transcribe_speech_sync(dest, [text])
    if new_stt.get("passed") is not True:
        raise RuntimeError(
            f"NARRATOR_TTS_STT_FAILED:{scene_id}:{new_stt.get('match_score')}:{new_stt.get('transcript')}"
        )
    audio = _audio_metrics(dest)
    if not audio.get("present") or not audio.get("non_silent"):
        raise RuntimeError(f"NARRATOR_TTS_AUDIO_INVALID:{scene_id}")
    embedding = speaker_embedding_from_video(dest, new_stt.get("segments"))
    return {
        "version": NARRATOR_TTS_VERSION,
        "project_id": project_id,
        "scene_id": scene_id,
        "base_media_id": media.get("id"),
        "base_media_version": media.get("version"),
        "base_media_path": str(src),
        "output_path": str(dest),
        "voice_id": voice_id,
        "provider": TTS_PROVIDER,
        "model": TTS_MODEL,
        "voiceover": text,
        "speech_windows": windows,
        "target_speech_duration": round(target_duration, 6),
        "tts_duration": round(tts_duration, 6),
        "atempo_factor": round(factor, 6),
        "tts_cache": tts,
        "original_stt": original_stt,
        "new_stt": new_stt,
        "audio_metrics": audio,
        "video_stream_sha256": output_hash,
        "video_stream_unchanged": True,
        "output_bytes": dest.stat().st_size,
        "_embedding": embedding,
    }


def preview_narrator_upgrade(
    project_id: str,
    *,
    voice_id: str = DEFAULT_NARRATOR_VOICE,
    speed: float = 1.0,
) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scene_ids = [
        str(scene.get("id"))
        for scene in (project.get("scenes") or [])
        if str(scene.get("voiceover") or "").strip()
        and (normalize_audio_requirements(scene, project).get("speakers") or []) == ["NARRATOR"]
    ]
    if not scene_ids:
        raise ValueError("NARRATOR_SCENES_MISSING")

    results = [
        compose_narrator_audio(project_id, scene_id, voice_id=voice_id, speed=speed)
        for scene_id in scene_ids
    ]
    pairwise = []
    for left, right in itertools.combinations(results, 2):
        similarity = cosine_similarity(
            np.asarray(left["_embedding"], dtype=np.float32),
            np.asarray(right["_embedding"], dtype=np.float32),
        )
        pairwise.append({
            "scene_a": left["scene_id"],
            "scene_b": right["scene_id"],
            "similarity": round(float(similarity), 6),
        })
    min_similarity = min((row["similarity"] for row in pairwise), default=None)
    ready = (
        all(row.get("video_stream_unchanged") for row in results)
        and all((row.get("new_stt") or {}).get("passed") is True for row in results)
        and all((row.get("audio_metrics") or {}).get("non_silent") is True for row in results)
        and (min_similarity is None or min_similarity >= SPEAKER_THRESHOLD)
    )

    public_rows = []
    for row in results:
        item = {key: value for key, value in row.items() if key != "_embedding"}
        public_rows.append(item)
    return {
        "version": NARRATOR_TTS_VERSION,
        "project_id": project_id,
        "provider": TTS_PROVIDER,
        "model": TTS_MODEL,
        "voice_id": voice_id,
        "scene_count": len(results),
        "same_voice_pairs": pairwise,
        "min_same_voice_similarity": min_similarity,
        "required_similarity": SPEAKER_THRESHOLD,
        "ready": bool(ready),
        "scenes": public_rows,
    }


def build_overlay_qc(base_media: dict, evidence: dict, speaker_item: dict | None = None) -> dict:
    """Reuse visual QC only when the video stream is bit-identical, then replace audio evidence."""
    if evidence.get("video_stream_unchanged") is not True:
        raise ValueError("NARRATOR_OVERLAY_VISUAL_INVARIANT_FAILED")
    base_qc = base_media.get("qc") if isinstance(base_media.get("qc"), dict) else {}
    qc = json.loads(json.dumps(base_qc, ensure_ascii=False))
    score = base_media.get("qc_score")
    if score is None:
        score = qc.get("consistency_score")
    qc["version"] = qc.get("version") or "video-qc-v2"
    qc["qc_status"] = "passed"
    qc["passed"] = True
    qc["consistency_score"] = score
    qc.setdefault("hard_gate", {"passed": True, "failed": []})
    qc["hard_gate"]["passed"] = True
    qc["hard_gate"]["failed"] = []

    ev = qc.setdefault("evidence", {})
    ev["audio"] = dict(evidence.get("audio_metrics") or {})
    ev["narrator_tts"] = {
        "version": evidence.get("version"),
        "provider": evidence.get("provider"),
        "model": evidence.get("model"),
        "voice_id": evidence.get("voice_id"),
        "base_media_id": evidence.get("base_media_id"),
        "video_stream_sha256": evidence.get("video_stream_sha256"),
        "video_stream_unchanged": evidence.get("video_stream_unchanged"),
        "speech_windows": evidence.get("speech_windows"),
        "tts_duration": evidence.get("tts_duration"),
        "target_speech_duration": evidence.get("target_speech_duration"),
        "atempo_factor": evidence.get("atempo_factor"),
    }

    raw = qc.setdefault("raw", {})
    speech = json.loads(json.dumps(evidence.get("new_stt") or {}, ensure_ascii=False))
    if speaker_item:
        speech["speaker_verification"] = dict(speaker_item)
    raw["speech_check"] = speech
    raw["audio_check"] = dict(evidence.get("audio_metrics") or {})

    issues = []
    for issue in qc.get("issues") or []:
        if str((issue or {}).get("type") or "").startswith("voice_identity"):
            continue
        issues.append(issue)
    if speaker_item and speaker_item.get("status") not in {"passed", "enrolled_reference", "not_required"}:
        issues.append({
            "type": "voice_identity",
            "severity": "high",
            "evidence": f"External TTS speaker verification status={speaker_item.get('status')}",
            "expected": "Fixed narrator voice must be fully calibrated and verified.",
        })
    qc["issues"] = issues
    qc["narrator_tts"] = {
        "provider": evidence.get("provider"),
        "model": evidence.get("model"),
        "voice_id": evidence.get("voice_id"),
        "fully_verified": bool(
            speaker_item
            and speaker_item.get("status") in {"passed", "enrolled_reference", "not_required"}
        ),
    }
    return qc


async def apply_narrator_upgrade(
    project_id: str,
    *,
    voice_id: str = DEFAULT_NARRATOR_VOICE,
    speed: float = 1.0,
) -> dict:
    """Apply a prevalidated audio-only narrator upgrade to selected media and rebuild acceptance."""
    from .film_acceptance_snapshot import create_acceptance_snapshot
    from .film_boundary_service import recheck_junctions_for_scene_async
    from .film_final_assembly import assemble_project
    from .film_master_qc import run_master_qc
    from .film_media_service import _probe_video, make_thumbnail, select_production_media
    from .film_media_store import (
        get_media,
        register_completed_media,
        update_media_qc,
    )
    from .film_prompt_policy_migration import build_policy_revalidation
    from .film_scene_state_store import (
        execution_lease_active,
        get_active_run,
        get_ledger,
        get_scene_state,
        save_ledger,
        upsert_scene_state,
    )
    from .film_speaker_acceptance import run_project_speaker_acceptance
    from .film_voice_profile_store import default_narrator_profile, upsert_voice_profile

    if get_active_run(project_id):
        raise ValueError("NARRATOR_TTS_PIPELINE_ACTIVE")
    if execution_lease_active(project_id):
        raise ValueError("NARRATOR_TTS_EXECUTION_LEASE_ACTIVE")

    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    preview = preview_narrator_upgrade(project_id, voice_id=voice_id, speed=speed)
    if preview.get("ready") is not True:
        raise ValueError("NARRATOR_TTS_PREVIEW_NOT_READY")

    profile = default_narrator_profile()
    profile.update({
        "provider": TTS_PROVIDER,
        "provider_voice_id": voice_id,
        "tts_model": TTS_MODEL,
        "voice_identity": (
            f"fixed xKiro voice id {voice_id}; deterministic narrator voice; "
            "same provider voice across every narration scene"
        ),
    })
    upsert_voice_profile(
        project_id,
        "NARRATOR",
        profile,
        provider=TTS_PROVIDER,
        provider_voice_id=voice_id,
    )

    registered: dict[str, dict] = {}
    evidence_by_scene = {row["scene_id"]: row for row in (preview.get("scenes") or [])}
    for scene_id, evidence in evidence_by_scene.items():
        base_media = get_media(str(evidence.get("base_media_id") or ""))
        if not base_media or base_media.get("is_selected") is not True:
            raise ValueError(f"NARRATOR_TTS_BASE_MEDIA_CHANGED:{scene_id}")
        output_path = Path(str(evidence.get("output_path") or ""))
        if not output_path.is_file():
            raise ValueError(f"NARRATOR_TTS_OUTPUT_MISSING:{scene_id}")
        probe = _probe_video(output_path)
        base_thumb = Path(str(base_media.get("thumbnail_path") or ""))
        thumb = make_thumbnail(
            output_path,
            "video",
            base_thumb if base_thumb.is_file() else None,
            key=f"narrator_tts_{scene_id}",
        )
        qc = build_overlay_qc(base_media, evidence)
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        record = register_completed_media(
            project_id=project_id,
            media_type="video",
            role="scene_video",
            file_path=output_path,
            scene_id=scene_id,
            provider=TTS_PROVIDER,
            model=TTS_MODEL,
            provider_job_id=f"narrator-tts:{project_id}:{scene_id}:{digest[:24]}",
            thumbnail_path=thumb,
            mime_type=probe.get("mime_type") or "video/mp4",
            file_size=output_path.stat().st_size,
            width=probe.get("width"),
            height=probe.get("height"),
            duration_seconds=probe.get("duration_seconds"),
            qc_status="passed",
            qc_score=base_media.get("qc_score"),
            qc=qc,
            metadata={
                "source": "narrator_tts_overlay",
                "tts_version": NARRATOR_TTS_VERSION,
                "base_media_id": base_media.get("id"),
                "base_media_version": base_media.get("version"),
                "video_stream_sha256": evidence.get("video_stream_sha256"),
                "video_stream_unchanged": True,
                "provider_voice_id": voice_id,
                "tts_model": TTS_MODEL,
                "download_name": output_path.name,
            },
            select_if_passed=False,
        )
        registered[scene_id] = record

    # Switch selection only after all overlay records have been created.
    for scene_id, media in registered.items():
        registered[scene_id] = select_production_media(media["id"])

    acceptance = run_project_speaker_acceptance(project_id, recalibrate=True)
    narrator_cal = (
        ((acceptance.get("calibration") or {}).get("speaker_calibrations") or {}).get("NARRATOR")
        or {}
    )
    if (
        acceptance.get("fully_verified") is not True
        or narrator_cal.get("status") != "calibrated"
    ):
        raise RuntimeError(
            "NARRATOR_TTS_SPEAKER_ACCEPTANCE_FAILED:"
            + json.dumps({
                "fully_verified": acceptance.get("fully_verified"),
                "narrator_calibration": narrator_cal,
            }, ensure_ascii=False)
        )

    item_by_scene = {
        str(item.get("scene_id")): item
        for item in (acceptance.get("items") or [])
        if isinstance(item, dict)
    }
    project = get_film_project(project_id) or project
    policy = build_policy_revalidation(project, acceptance)
    upgraded = []

    for scene_id, evidence in evidence_by_scene.items():
        media = registered[scene_id]
        base_media = get_media(str(evidence.get("base_media_id") or "")) or {}
        speaker_item = item_by_scene.get(scene_id) or {}
        if speaker_item.get("status") not in {"passed", "enrolled_reference", "not_required"}:
            raise RuntimeError(f"NARRATOR_TTS_SCENE_NOT_VERIFIED:{scene_id}:{speaker_item.get('status')}")
        qc = build_overlay_qc(base_media, evidence, speaker_item)
        media = update_media_qc(
            media["id"],
            qc_status="passed",
            qc=qc,
            qc_score=base_media.get("qc_score"),
        )
        media = select_production_media(media["id"])

        state = get_scene_state(project_id, scene_id) or {}
        ledger = dict(get_ledger(project_id, scene_id) or {})
        ledger["selected_media_id"] = media["id"]
        ledger["dialogue_state"] = "delivered"
        ledger["audio_state"] = dict(evidence.get("audio_metrics") or {})
        snap = dict(ledger.get("snapshot") or {})
        snap.update({
            "provider": TTS_PROVIDER,
            "model": TTS_MODEL,
            "qc_status": "passed",
            "consistency_score": base_media.get("qc_score"),
            "media_id": media["id"],
            "media_version": media.get("version"),
            "narrator_voice_id": voice_id,
            "tts_version": NARRATOR_TTS_VERSION,
        })
        ledger["snapshot"] = snap
        ledger = save_ledger(project_id, scene_id, ledger)

        scene = next(
            row for row in (project.get("scenes") or [])
            if row.get("id") == scene_id
        )
        upsert_scene_state(
            project_id,
            scene_id,
            int(scene.get("scene_index") or state.get("scene_index") or 0),
            status="APPROVED",
            selected_media_id=media["id"],
            best_media_id=media["id"],
            best_score=base_media.get("qc_score"),
            qc=qc,
            error=None,
            blocked_reason=None,
            snapshot={
                "ledger": ledger,
                "media_id": media["id"],
                "narrator_tts": {
                    "provider": TTS_PROVIDER,
                    "model": TTS_MODEL,
                    "voice_id": voice_id,
                    "speaker_status": speaker_item.get("status"),
                    "speaker_similarity": speaker_item.get("speaker_similarity"),
                },
            },
            force=True,
        )
        snapshot = create_acceptance_snapshot(
            project_id,
            scene_id,
            policy_revalidation=policy.get(scene_id) or {},
        )
        upgraded.append({
            "scene_id": scene_id,
            "media_id": media["id"],
            "media_version": media.get("version"),
            "speaker_status": speaker_item.get("status"),
            "speaker_similarity": speaker_item.get("speaker_similarity"),
            "snapshot_id": snapshot.get("id"),
        })

    junction_rows: dict[str, dict] = {}
    for scene_id in evidence_by_scene:
        for row in await recheck_junctions_for_scene_async(project_id, scene_id):
            junction_rows[str(row.get("id") or f"{row.get('previous_scene_id')}->{row.get('next_scene_id')}")] = row
    junction_fail = [row for row in junction_rows.values() if row.get("status") != "PASS"]
    if junction_fail:
        raise RuntimeError(
            "NARRATOR_TTS_JUNCTION_FAILED:"
            + json.dumps([
                {
                    "previous_scene_id": row.get("previous_scene_id"),
                    "next_scene_id": row.get("next_scene_id"),
                    "status": row.get("status"),
                    "error": row.get("error"),
                }
                for row in junction_fail
            ], ensure_ascii=False)
        )

    assembled = assemble_project(project_id)
    current = assembled.get("current") or {}
    if current.get("status") in {"QC_PENDING", "QC_FAILED"}:
        assembled = run_master_qc(project_id, current.get("id"))
        current = assembled.get("current") or {}
    if current.get("status") != "APPROVED":
        raise RuntimeError(f"NARRATOR_TTS_FINAL_NOT_APPROVED:{current.get('status')}")

    return {
        "version": NARRATOR_TTS_VERSION,
        "project_id": project_id,
        "voice_id": voice_id,
        "provider": TTS_PROVIDER,
        "model": TTS_MODEL,
        "preview": {
            "ready": preview.get("ready"),
            "min_same_voice_similarity": preview.get("min_same_voice_similarity"),
            "required_similarity": preview.get("required_similarity"),
        },
        "speaker_acceptance": acceptance,
        "upgraded_scenes": upgraded,
        "junctions": list(junction_rows.values()),
        "final": assembled,
    }
