from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import DATA_DIR
from .film_audio_schema import normalize_audio_requirements
from .film_voice_profile_store import (
    clear_voice_acoustic_evidence,
    get_voice_profile,
    update_voice_acoustic_evidence,
)

SPEAKER_ENABLED = os.getenv("FILM_SPEAKER_VERIFY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
SPEAKER_REQUIRED = os.getenv("FILM_SPEAKER_VERIFY_REQUIRED", "1").strip().lower() not in {"0", "false", "no", "off"}
SPEAKER_THRESHOLD = float(os.getenv("FILM_SPEAKER_VERIFY_THRESHOLD", "0.60"))
SPEAKER_CALIBRATION_MIN_GAP = float(os.getenv("FILM_SPEAKER_CALIBRATION_MIN_GAP", "0.05"))
SPEAKER_MIN_SECONDS = float(os.getenv("FILM_SPEAKER_MIN_SECONDS", "0.80"))
SPEAKER_MIN_CHUNK_SECONDS = float(os.getenv("FILM_SPEAKER_MIN_CHUNK_SECONDS", "0.65"))
SPEAKER_SAMPLE_RATE = 16000
SPEAKER_STRATEGY = "whisper-word-silero-vad-longest-clean-chunk"
DEFAULT_MODEL_PATH = DATA_DIR / "models" / "speaker" / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
SPEAKER_MODEL_PATH = Path(os.getenv("FILM_SPEAKER_MODEL_PATH", str(DEFAULT_MODEL_PATH))).expanduser()

_EXTRACTOR = None
_EXTRACTOR_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._") or "speaker"


def _speaker_project_dir(project_id: str) -> Path:
    folder = DATA_DIR / "speaker_embeddings" / _safe_id(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _embedding_path(project_id: str, character_id: str) -> Path:
    return _speaker_project_dir(project_id) / f"{_safe_id(character_id)}.npy"


def _calibration_path(project_id: str) -> Path:
    return _speaker_project_dir(project_id) / "_calibration.json"


def load_project_calibration(project_id: str) -> dict | None:
    path = _calibration_path(project_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("model") != SPEAKER_MODEL_PATH.name:
        return None
    if data.get("embedding_strategy") != SPEAKER_STRATEGY:
        return None
    if data.get("status") not in {"calibrated", "partial"}:
        return None
    return data


def save_project_calibration(project_id: str, report: dict) -> dict:
    payload = dict(report or {})
    payload["project_id"] = project_id
    payload["model"] = SPEAKER_MODEL_PATH.name
    payload["embedding_strategy"] = SPEAKER_STRATEGY
    payload["updated_at"] = _now()
    path = _calibration_path(project_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def speaker_threshold_for_project(project_id: str, speaker_id: str | None = None) -> tuple[float, str, dict | None]:
    calibration = load_project_calibration(project_id)
    if calibration:
        if speaker_id:
            rows = calibration.get("speaker_calibrations")
            row = rows.get(speaker_id) if isinstance(rows, dict) else None
            if isinstance(row, dict) and row.get("status") == "calibrated":
                try:
                    threshold = float(row.get("threshold"))
                except Exception:
                    threshold = None
                if threshold is not None and 0.0 < threshold < 1.0:
                    return threshold, "speaker_project_calibrated", calibration
            if isinstance(row, dict) and row.get("status") in {"ambiguous", "insufficient_samples"}:
                return SPEAKER_THRESHOLD, f"speaker_{row.get('status')}_not_enforced", calibration
        try:
            threshold = float(calibration.get("threshold"))
        except Exception:
            threshold = None
        if threshold is not None and 0.0 < threshold < 1.0:
            return threshold, "project_calibrated", calibration
    return SPEAKER_THRESHOLD, "official_fallback_not_enforced", calibration


def _relative_embedding_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(DATA_DIR.resolve())).replace("\\", "/")
    except Exception:
        return path.name


def _resolve_embedding_path(relative_path: str | None, project_id: str, character_id: str) -> Path:
    if relative_path:
        candidate = (DATA_DIR / str(relative_path)).resolve()
        try:
            candidate.relative_to(DATA_DIR.resolve())
            return candidate
        except ValueError:
            pass
    return _embedding_path(project_id, character_id)


def speaker_identity_status() -> dict:
    return {
        "enabled": SPEAKER_ENABLED,
        "required": SPEAKER_REQUIRED,
        "model_path": str(SPEAKER_MODEL_PATH),
        "model_exists": SPEAKER_MODEL_PATH.is_file(),
        "threshold": SPEAKER_THRESHOLD,
        "sample_rate": SPEAKER_SAMPLE_RATE,
        "min_seconds": SPEAKER_MIN_SECONDS,
        "backend": "sherpa-onnx",
        "embedding_strategy": SPEAKER_STRATEGY,
    }


def _get_extractor():
    global _EXTRACTOR
    if not SPEAKER_ENABLED:
        raise RuntimeError("SPEAKER_VERIFICATION_DISABLED")
    if not SPEAKER_MODEL_PATH.is_file():
        raise RuntimeError(f"SPEAKER_MODEL_MISSING: {SPEAKER_MODEL_PATH}")
    with _EXTRACTOR_LOCK:
        if _EXTRACTOR is None:
            import sherpa_onnx

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(SPEAKER_MODEL_PATH),
                num_threads=max(1, min(int(os.getenv("FILM_SPEAKER_THREADS", "2")), 8)),
                debug=False,
                provider="cpu",
            )
            if not config.validate():
                raise RuntimeError("SPEAKER_MODEL_INVALID")
            _EXTRACTOR = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _EXTRACTOR


def _decode_audio(video_path: Path) -> np.ndarray:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SPEAKER_SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = (proc.stderr or b"").decode("utf-8", "ignore")[:600]
        raise RuntimeError(f"SPEAKER_AUDIO_DECODE_FAILED: {detail}")
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return np.ascontiguousarray(samples)


def _word_regions(segments: list[dict] | None, total: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    pad = int(float(os.getenv("FILM_SPEAKER_WORD_PAD_MS", "80")) * SPEAKER_SAMPLE_RATE / 1000.0)
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        words = segment.get("words") if isinstance(segment.get("words"), list) else []
        source = words or [segment]
        for item in source:
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start") or 0.0)
                end = float(item.get("end") or start)
            except Exception:
                continue
            a = max(0, min(total, int(start * SPEAKER_SAMPLE_RATE) - pad))
            b = max(a, min(total, int(end * SPEAKER_SAMPLE_RATE) + pad))
            if b > a:
                ranges.append((a, b))
    if not ranges:
        return []
    ranges.sort()
    merged: list[list[int]] = []
    merge_gap = int(0.18 * SPEAKER_SAMPLE_RATE)
    for a, b in ranges:
        if not merged or a > merged[-1][1] + merge_gap:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return [(a, b) for a, b in merged]


def _vad_speech_chunks(samples: np.ndarray, segments: list[dict] | None = None) -> list[np.ndarray]:
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        vad_chunks = get_speech_timestamps(
            samples,
            VadOptions(
                threshold=float(os.getenv("FILM_SPEAKER_VAD_THRESHOLD", "0.5")),
                min_speech_duration_ms=250,
                min_silence_duration_ms=350,
                speech_pad_ms=120,
            ),
            sampling_rate=SPEAKER_SAMPLE_RATE,
        )
    except Exception as exc:
        raise RuntimeError(f"SPEAKER_VAD_FAILED: {exc}") from exc

    total = len(samples)
    min_chunk = int(SPEAKER_MIN_CHUNK_SECONDS * SPEAKER_SAMPLE_RATE)
    word_regions = _word_regions(segments, total)
    chunks: list[np.ndarray] = []

    for item in vad_chunks:
        try:
            vad_a = max(0, min(total, int(item.get("start") or 0)))
            vad_b = max(vad_a, min(total, int(item.get("end") or vad_a)))
        except Exception:
            continue
        intersections = [(vad_a, vad_b)] if not word_regions else [
            (max(vad_a, word_a), min(vad_b, word_b))
            for word_a, word_b in word_regions
            if min(vad_b, word_b) > max(vad_a, word_a)
        ]
        for a, b in intersections:
            if b - a >= min_chunk:
                chunks.append(np.ascontiguousarray(samples[a:b]))

    if not chunks:
        raise RuntimeError("SPEAKER_VAD_WORD_INTERSECTION_EMPTY" if word_regions else "SPEAKER_VAD_NO_USABLE_SPEECH")
    return chunks


def _embedding_from_samples(samples: np.ndarray) -> np.ndarray:
    extractor = _get_extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=SPEAKER_SAMPLE_RATE, waveform=np.ascontiguousarray(samples))
    stream.input_finished()
    if not extractor.is_ready(stream):
        raise RuntimeError("SPEAKER_EMBEDDING_NOT_READY")
    embedding = np.asarray(extractor.compute(stream), dtype=np.float32)
    norm = float(np.linalg.norm(embedding))
    if embedding.ndim != 1 or not embedding.size or norm <= 1e-8:
        raise RuntimeError("SPEAKER_EMBEDDING_INVALID")
    return embedding / norm


def speaker_embedding_from_video(video_path: str | Path, segments: list[dict] | None = None) -> np.ndarray:
    path = Path(video_path)
    if not path.is_file():
        raise RuntimeError("SPEAKER_VIDEO_NOT_FOUND")

    chunks = _vad_speech_chunks(_decode_audio(path), segments)
    longest = max(chunks, key=len)
    clean_seconds = len(longest) / float(SPEAKER_SAMPLE_RATE)
    if clean_seconds < SPEAKER_MIN_SECONDS:
        raise RuntimeError(f"SPEAKER_AUDIO_TOO_SHORT: {clean_seconds:.3f}s")
    return _embedding_from_samples(longest)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    va = np.asarray(a, dtype=np.float32).reshape(-1)
    vb = np.asarray(b, dtype=np.float32).reshape(-1)
    if va.size != vb.size or not va.size:
        raise ValueError("SPEAKER_EMBEDDING_DIM_MISMATCH")
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom <= 1e-8:
        raise ValueError("SPEAKER_EMBEDDING_ZERO_NORM")
    return max(-1.0, min(1.0, float(np.dot(va, vb) / denom)))


def _save_reference(project_id: str, character_id: str, embedding: np.ndarray, scene_id: str) -> dict:
    path = _embedding_path(project_id, character_id)
    np.save(path, np.asarray(embedding, dtype=np.float32), allow_pickle=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    threshold, threshold_source, _ = speaker_threshold_for_project(project_id, character_id)
    evidence = {
        "status": "enrolled",
        "backend": "sherpa-onnx",
        "model": SPEAKER_MODEL_PATH.name,
        "embedding_dim": int(embedding.size),
        "embedding_sha256": digest,
        "embedding_path": _relative_embedding_path(path),
        "enrolled_scene_id": scene_id,
        "enrolled_at": _now(),
        "threshold": threshold,
        "threshold_source": threshold_source,
        "sample_rate": SPEAKER_SAMPLE_RATE,
        "embedding_strategy": SPEAKER_STRATEGY,
    }
    update_voice_acoustic_evidence(project_id, character_id, evidence)
    return evidence


def _load_reference(project_id: str, character_id: str) -> tuple[np.ndarray | None, dict]:
    row = get_voice_profile(project_id, character_id) or {}
    acoustic = dict((row.get("profile") or {}).get("acoustic_identity") or {})
    path = _resolve_embedding_path(acoustic.get("embedding_path"), project_id, character_id)
    if not acoustic or not path.is_file():
        return None, acoustic
    if (
        acoustic.get("model") != SPEAKER_MODEL_PATH.name
        or acoustic.get("embedding_strategy") != SPEAKER_STRATEGY
    ):
        acoustic["stale_reference"] = True
        acoustic["stale_reference_reason"] = "SPEAKER_MODEL_OR_STRATEGY_CHANGED"
        return None, acoustic
    embedding = np.load(path, allow_pickle=False)
    return np.asarray(embedding, dtype=np.float32).reshape(-1), acoustic


def verify_or_enroll_scene_speaker(
    project: dict,
    scene: dict,
    video_path: str | Path,
    speech_check: dict | None,
) -> dict:
    threshold = SPEAKER_THRESHOLD
    threshold_source = "official_fallback_not_enforced"
    calibration = None
    result = {
        "available": False,
        "status": "not_evaluated",
        "backend": "sherpa-onnx",
        "model": SPEAKER_MODEL_PATH.name,
        "embedding_strategy": SPEAKER_STRATEGY,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "calibrated": False,
    }
    if not SPEAKER_ENABLED:
        result.update({"status": "disabled", "error": "SPEAKER_VERIFICATION_DISABLED"})
        return result

    req = normalize_audio_requirements(scene, project)
    speakers = []
    for value in req.get("speakers") or []:
        cid = str(value or "").strip()
        if cid and cid not in speakers:
            speakers.append(cid)
    if not speakers:
        result.update({"available": True, "status": "not_required"})
        return result
    if len(speakers) != 1:
        result.update({
            "available": True,
            "status": "not_evaluated",
            "error": "MULTI_SPEAKER_DIARIZATION_REQUIRED",
            "speakers": speakers,
        })
        return result

    character_id = speakers[0]
    result["speaker_character_id"] = character_id
    threshold, threshold_source, calibration = speaker_threshold_for_project(
        str(project.get("id") or ""),
        character_id,
    )
    result["threshold"] = threshold
    result["threshold_source"] = threshold_source
    result["calibrated"] = threshold_source in {"speaker_project_calibrated", "project_calibrated"}
    if not isinstance(speech_check, dict) or speech_check.get("passed") is not True:
        result["error"] = "SPEAKER_STT_NOT_VERIFIED"
        return result

    try:
        embedding = speaker_embedding_from_video(video_path, speech_check.get("segments"))
        reference, acoustic = _load_reference(project["id"], character_id)
        result.update({
            "available": True,
            "embedding_dim": int(embedding.size),
            "speech_seconds": round(
                sum(
                    max(0.0, float(x.get("end") or 0) - float(x.get("start") or 0))
                    for x in (speech_check.get("segments") or [])
                    if isinstance(x, dict)
                ),
                3,
            ),
        })
        if reference is None:
            enrolled = _save_reference(project["id"], character_id, embedding, str(scene.get("id") or ""))
            result.update({
                "status": "enrolled_reference",
                "reference_created": True,
                "reference": {k: v for k, v in enrolled.items() if k != "embedding_path"},
                "speaker_similarity": None,
                "passed": None,
            })
            return result

        similarity = cosine_similarity(reference, embedding)
        enforce_threshold = threshold_source in {"speaker_project_calibrated", "project_calibrated"}
        if not enforce_threshold:
            pending_status = (
                "calibration_ambiguous"
                if "ambiguous" in threshold_source
                else "calibration_pending"
            )
            result.update({
                "status": pending_status,
                "reference_created": False,
                "reference_scene_id": acoustic.get("enrolled_scene_id"),
                "raw_similarity": round(similarity, 6),
                "raw_similarity_percent": round(similarity * 100.0, 2),
                "speaker_similarity": None,
                "passed": None,
            })
            return result

        passed = similarity >= threshold
        result.update({
            "status": "passed" if passed else "failed",
            "reference_created": False,
            "reference_scene_id": acoustic.get("enrolled_scene_id"),
            "raw_similarity": round(similarity, 6),
            "speaker_similarity": round(similarity, 6),
            "speaker_similarity_percent": round(similarity * 100.0, 2),
            "passed": passed,
        })
        return result
    except Exception as exc:
        result.update({"status": "error", "error": str(exc)[:1200]})
        return result


def reset_speaker_reference(project_id: str, character_id: str) -> dict:
    path = _embedding_path(project_id, character_id)
    if path.exists():
        path.unlink()
    clear_voice_acoustic_evidence(project_id, character_id)
    return {"project_id": project_id, "character_id": character_id, "reset": True}


def reset_project_speaker_calibration(project_id: str) -> dict:
    path = _calibration_path(project_id)
    if path.exists():
        path.unlink()
    return {"project_id": project_id, "calibration_reset": True}
