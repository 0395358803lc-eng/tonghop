import asyncio
import base64
import json
import os
import re
import subprocess
import threading
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import av
import httpx

from .config import DATA_DIR
from .provider_store import get_provider, list_saved
from .film_resource_store import scene_resource_manifest
from .film_speaker_identity import SPEAKER_REQUIRED, verify_or_enroll_scene_speaker
from .providers.vision import run_vision
from .vision_service import FREE_VISION_FALLBACKS, delete_video_artifacts, extract_keyframes, load_frame_payloads

QC_URL = os.getenv("FILM_QC_API_URL", "").strip()
QC_KEY = os.getenv("FILM_QC_API_KEY", "").strip()
QC_MIN_SCORE = float(os.getenv("FILM_QC_MIN_SCORE", "80"))
QC_PROVIDER = os.getenv("FILM_QC_PROVIDER", "xkiro").strip() or "xkiro"
QC_MODEL = os.getenv("FILM_QC_MODEL", "").strip()
QC_STT_ENABLED = os.getenv("FILM_QC_STT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
QC_STT_MODEL = os.getenv("FILM_QC_STT_MODEL", "base").strip() or "base"
QC_STT_MIN_MATCH = float(os.getenv("FILM_QC_STT_MIN_MATCH", "0.72"))
QC_STT_REQUIRE_MATCH = os.getenv("FILM_QC_STT_REQUIRE_MATCH", "1").strip().lower() not in {"0", "false", "no", "off"}
QC_IDENTITY_MIN = float(os.getenv("FILM_QC_IDENTITY_MIN", "90"))
QC_WARDROBE_MIN = float(os.getenv("FILM_QC_WARDROBE_MIN", "85"))
QC_LOCATION_MIN = float(os.getenv("FILM_QC_LOCATION_MIN", "82"))
QC_PROP_MIN = float(os.getenv("FILM_QC_PROP_MIN", "85"))
QC_BOUNDARY_MIN = float(os.getenv("FILM_QC_BOUNDARY_MIN", "88"))
_STT_MODEL = None
_STT_LOCK = threading.Lock()


def _provider_configured() -> bool:
    return QC_PROVIDER in list_saved()


def get_qc_status() -> dict:
    if QC_URL:
        return {
            "id": "http_json_qc",
            "name": "Custom HTTP Vision QC",
            "configured": True,
            "min_score": QC_MIN_SCORE,
            "contract": "POST JSON -> consistency_score/issues/passed",
        }
    configured = _provider_configured()
    return {
        "id": "builtin_vision_qc" if configured else "none",
        "name": f"{QC_PROVIDER} Vision QC" if configured else "Chưa cấu hình QC",
        "configured": configured,
        "min_score": QC_MIN_SCORE,
        "provider": QC_PROVIDER if configured else None,
        "model": QC_MODEL or None,
        "contract": "local video -> keyframes -> provider vision JSON" if configured else None,
    }


def _flow_video_path(result_url: str | None) -> Path | None:
    match = re.search(r"/api/flow/render/([0-9a-fA-F-]{36})/file$", result_url or "")
    if not match:
        return None
    folder = DATA_DIR / "flow_downloads" / match.group(1)
    for path in sorted(folder.glob("result.*")):
        if path.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"} and path.is_file():
            return path
    return None


def _audio_metrics(video_path: Path) -> dict:
    container = av.open(str(video_path))
    try:
        present = any(stream.type == "audio" for stream in container.streams)
    finally:
        container.close()
    if not present:
        return {"present": False, "non_silent": False, "mean_volume_db": None, "max_volume_db": None}

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path), "-af", "volumedetect", "-f", "null", os.devnull],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=45,
    )
    text = proc.stderr or ""
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", text)
    max_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", text)
    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None
    non_silent = max_db is not None and max_db > -50.0
    return {
        "present": True,
        "non_silent": non_silent,
        "mean_volume_db": mean_db,
        "max_volume_db": max_db,
    }


def _audio_present(video_path: Path) -> bool:
    return bool(_audio_metrics(video_path).get("present"))


def _normalize_speech(text: str) -> str:
    value = (text or "").lower()
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _expected_speech_lines(scene: dict) -> list[str]:
    lines: list[str] = []
    voiceover = str(scene.get("voiceover") or "").strip()
    if voiceover:
        lines.append(voiceover)
    for item in scene.get("dialogue") or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("line") or item.get("dialogue") or item.get("content") or "").strip()
        else:
            text = ""
        if text:
            lines.append(text)
    return lines


def _speech_line_match(expected: str, transcript: str) -> dict:
    exp = _normalize_speech(expected)
    actual = _normalize_speech(transcript)
    if not exp:
        return {"expected": expected, "passed": True, "score": 1.0, "exact": True, "token_coverage": 1.0, "similarity": 1.0}
    exact = exp in actual
    exp_tokens = exp.split()
    act_counts = Counter(actual.split())
    exp_counts = Counter(exp_tokens)
    overlap = sum(min(count, act_counts.get(token, 0)) for token, count in exp_counts.items())
    token_coverage = overlap / max(1, sum(exp_counts.values()))
    similarity = SequenceMatcher(None, exp, actual).ratio()
    score = max(1.0 if exact else 0.0, token_coverage, similarity)
    return {
        "expected": expected,
        "passed": bool(exact or token_coverage >= 0.85 or similarity >= QC_STT_MIN_MATCH),
        "score": round(score, 4),
        "exact": exact,
        "token_coverage": round(token_coverage, 4),
        "similarity": round(similarity, 4),
    }


def _transcribe_speech_sync(video_path: Path, expected_lines: list[str]) -> dict:
    global _STT_MODEL
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        return {"available": False, "passed": False, "error": f"faster_whisper unavailable: {exc}"}

    with _STT_LOCK:
        if _STT_MODEL is None:
            _STT_MODEL = WhisperModel(QC_STT_MODEL, device="cpu", compute_type="int8")
        segments, info = _STT_MODEL.transcribe(
            str(video_path),
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        segment_rows = []
        transcript_parts = []
        for segment in segments:
            text = str(segment.text or "").strip()
            if not text:
                continue
            transcript_parts.append(text)
            word_rows = []
            for word in (getattr(segment, "words", None) or []):
                token = str(getattr(word, "word", "") or "").strip()
                if not token:
                    continue
                word_rows.append({
                    "start": round(float(getattr(word, "start", 0.0) or 0.0), 3),
                    "end": round(float(getattr(word, "end", 0.0) or 0.0), 3),
                    "word": token,
                    "probability": round(float(getattr(word, "probability", 0.0) or 0.0), 4),
                })
            segment_rows.append({
                "start": round(float(getattr(segment, "start", 0.0) or 0.0), 3),
                "end": round(float(getattr(segment, "end", 0.0) or 0.0), 3),
                "text": text,
                "words": word_rows,
            })
        transcript = " ".join(transcript_parts).strip()

    matches = [_speech_line_match(line, transcript) for line in expected_lines]
    passed = bool(matches) and all(item["passed"] for item in matches)
    average = sum(float(item["score"]) for item in matches) / max(1, len(matches))
    return {
        "available": True,
        "model": QC_STT_MODEL,
        "language": getattr(info, "language", None),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 4),
        "transcript": transcript,
        "normalized_transcript": _normalize_speech(transcript),
        "segments": segment_rows,
        "expected_lines": expected_lines,
        "matches": matches,
        "match_score": round(average, 4),
        "passed": passed,
    }


async def _transcribe_speech(video_path: Path, expected_lines: list[str]) -> dict:
    return await asyncio.to_thread(_transcribe_speech_sync, video_path, expected_lines)


def _expected_context(project: dict, scene: dict, job: dict) -> dict:
    character_ids = set(scene.get("characters") or [])
    characters = [
        item for item in (project.get("characters") or [])
        if not character_ids or str(item.get("id") or item.get("character_id") or "") in character_ids
    ]
    location_id = scene.get("location_id")
    locations = [
        item for item in (project.get("locations") or [])
        if not location_id or str(item.get("id") or item.get("location_id") or "") == str(location_id)
    ]
    prop_ids = set(scene.get("props_present") or [])
    props = [
        item for item in (project.get("props") or [])
        if not prop_ids or str(item.get("id") or item.get("prop_id") or "") in prop_ids
    ]
    return {
        "scene_id": scene.get("id"),
        "flow_prompt": scene.get("flow_prompt") or scene.get("visual_prompt"),
        "visual_style": project.get("visual_style"),
        "characters": characters[:12],
        "locations": locations[:6],
        "props": props[:12],
        "start_state": scene.get("start_state"),
        "end_state": scene.get("end_state"),
        "dialogue": scene.get("dialogue") or [],
        "voiceover": scene.get("voiceover"),
        "reference": job.get("reference") or {},
    }


def _flow_frame_path(url: str | None, kind: str) -> Path | None:
    match = re.search(r"/api/flow/render/([0-9a-fA-F-]{36})/(?:last-frame|first-frame)$", url or "")
    if not match:
        return None
    folder = DATA_DIR / "flow_downloads" / match.group(1)
    pattern = "first_frame_*.jpg" if kind == "first" else "last_frame_*.jpg"
    matches = sorted(folder.glob(pattern))
    return matches[0] if matches else None


def _image_payload(path: Path, label: str) -> dict | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        return {
            "timestamp": 0.0,
            "label": label,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    except Exception:
        return None


def _visual_reference_payloads(project: dict, scene: dict, job: dict) -> tuple[list[dict], dict]:
    manifest = scene_resource_manifest(project, scene, "flow")
    payloads: list[dict] = []
    evidence = {
        "manifest_version": manifest.get("version"),
        "canonical": [],
        "previous_boundary": None,
        "current_boundary": [],
    }

    for item in manifest.get("references") or []:
        raw = str(item.get("local_path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        kind = str(item.get("resource_type") or "other")
        entity_id = str(item.get("entity_id") or "unknown")
        payload = _image_payload(path, f"CANONICAL {kind.upper()} {entity_id}")
        if payload:
            payloads.append(payload)
            evidence["canonical"].append({
                "resource_type": kind,
                "entity_id": entity_id,
                "asset_version": item.get("asset_version"),
                "canonical_sha256": item.get("canonical_sha256"),
            })

    reference = job.get("reference") or {}
    previous = _flow_frame_path(reference.get("previous_last_frame_url"), "last")
    if previous:
        payload = _image_payload(previous, f"PREVIOUS SCENE ACCEPTED LAST FRAME {reference.get('previous_scene_id') or ''}".strip())
        if payload:
            payloads.append(payload)
            evidence["previous_boundary"] = str(reference.get("previous_scene_id") or "")

    current_first = _flow_frame_path(job.get("first_frame_url"), "first")
    if current_first:
        payload = _image_payload(current_first, "CURRENT SCENE FIRST FRAME")
        if payload:
            payloads.append(payload)
            evidence["current_boundary"].append("first")

    current_last = _flow_frame_path(job.get("last_frame_url"), "last")
    if current_last:
        payload = _image_payload(current_last, "CURRENT SCENE LAST FRAME")
        if payload:
            payloads.append(payload)
            evidence["current_boundary"].append("last")

    return payloads, evidence


def _required_visual_dimensions(scene: dict, job: dict) -> dict[str, float]:
    required: dict[str, float] = {}
    if scene.get("characters"):
        required["identity"] = QC_IDENTITY_MIN
        required["wardrobe"] = QC_WARDROBE_MIN
    if scene.get("location_id"):
        required["location"] = QC_LOCATION_MIN
    if scene.get("props_present"):
        required["prop"] = QC_PROP_MIN
    if (job.get("reference") or {}).get("previous_scene_id"):
        required["boundary"] = QC_BOUNDARY_MIN
    return required


def _apply_visual_hard_gates(data: dict, scene: dict, job: dict) -> dict:
    dimensions = data.get("dimension_scores")
    if not isinstance(dimensions, dict):
        dimensions = {}
        data["dimension_scores"] = dimensions

    hard_results = {}
    for name, threshold in _required_visual_dimensions(scene, job).items():
        raw = dimensions.get(name)
        try:
            score = float(raw)
        except Exception:
            score = None
        passed = score is not None and score >= threshold
        hard_results[name] = {"score": score, "threshold": threshold, "passed": passed}
        if not passed:
            data["passed"] = False
            data["consistency_score"] = min(float(data.get("consistency_score") or 0), QC_MIN_SCORE - 1)
            data.setdefault("issues", []).append({
                "type": name,
                "severity": "critical",
                "evidence": "Thiếu điểm xác minh" if score is None else f"{name} score={score:.1f} thấp hơn ngưỡng {threshold:.1f}.",
                "expected": f"{name} phải đạt tối thiểu {threshold:.1f}/100 dựa trên visual reference thực tế.",
            })

    data["hard_gate"] = {
        "version": "visual-continuity-v2",
        "passed": all(item["passed"] for item in hard_results.values()),
        "dimensions": hard_results,
    }
    if not data["hard_gate"]["passed"]:
        data["passed"] = False
    return data


def _json_from_text(text: str) -> dict:
    raw = (text or "").strip()
    ticks = chr(96) * 3
    if raw.startswith(ticks + "json"):
        raw = raw[len(ticks + "json"):].strip()
    elif raw.startswith(ticks):
        raw = raw[len(ticks):].strip()
    if raw.endswith(ticks):
        raw = raw[:-len(ticks)].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Vision model không trả JSON object.")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("Vision QC JSON không hợp lệ.")
    score = data.get("consistency_score", data.get("score"))
    if score is None:
        raise ValueError("Vision QC thiếu consistency_score.")
    score = max(0.0, min(100.0, float(score)))
    data["consistency_score"] = score
    issues = data.get("issues")
    if not isinstance(issues, list):
        data["issues"] = []
    passed = data.get("passed")
    if not isinstance(passed, bool):
        data["passed"] = score >= QC_MIN_SCORE
    return data


def _qc_prompt(context: dict, audio_required: bool, audio_metrics: dict, required_dimensions: dict[str, float]) -> str:
    expected = json.dumps(context, ensure_ascii=False)
    thresholds = json.dumps(required_dimensions, ensure_ascii=False)
    return f"""Bạn là Vision QC cho pipeline tạo phim nhiều cảnh có Visual Continuity Lock V2.
Trong danh sách ảnh có thể có:
- CANONICAL CHARACTER/LOCATION/PROP: ảnh chuẩn bị khóa cứng.
- PREVIOUS SCENE ACCEPTED LAST FRAME: khung cuối cảnh trước đã QC PASS.
- CURRENT SCENE FIRST/LAST FRAME và các frame video hiện tại.

Hãy so sánh TRỰC TIẾP hình ảnh hiện tại với canonical references và boundary frame. Không được chỉ dựa vào mô tả text.
Chỉ dùng chi tiết thực sự nhìn thấy. Không bịa nội dung, không suy đoán lời thoại.
Các dimension bắt buộc và ngưỡng backend sẽ áp dụng: {thresholds}

Bắt buộc trả điểm 0-100 cho mọi dimension có trong danh sách ngưỡng:
- identity: cùng đúng nhân vật/khuôn mặt/đặc điểm nhận diện.
- wardrobe: quần áo/phụ kiện đúng trạng thái.
- location: cùng bối cảnh, layout, kiến trúc, vật cố định và ánh sáng hợp lý.
- prop: đạo cụ đúng hình dạng/trạng thái/chủ sở hữu khi nhìn thấy.
- boundary: first frame cảnh hiện tại nối hợp lý với accepted last frame cảnh trước.
Ngoài ra đánh giá prompt/style/camera và tổng thể.
Audio required={str(audio_required).lower()}, audio metrics={json.dumps(audio_metrics, ensure_ascii=False)}.
Nếu audio_required=true nhưng audio stream absent hoặc non_silent=false, phải ghi issue severity=critical và passed=false.

EXPECTED_CONTEXT:
{expected}

Chỉ trả về JSON object, không markdown:
{{
  "consistency_score": 0,
  "passed": false,
  "dimension_scores": {{
    "identity": 0,
    "wardrobe": 0,
    "location": 0,
    "prop": 0,
    "boundary": 0,
    "camera": 0,
    "lighting": 0,
    "audio": 0
  }},
  "observed_summary": "...",
  "character_consistency": "...",
  "location_consistency": "...",
  "continuity_consistency": "...",
  "prompt_alignment": "...",
  "audio_check": {{"required": {str(audio_required).lower()}, "present": {str(bool(audio_metrics.get('present'))).lower()}, "non_silent": {str(bool(audio_metrics.get('non_silent'))).lower()}}},
  "issues": [
    {{"type":"identity|wardrobe|location|prop|boundary|prompt|audio|other","severity":"low|medium|high|critical","evidence":"...","expected":"..."}}
  ]
}}"""


async def _run_builtin_qc(job: dict, project: dict, scene: dict) -> dict:
    credentials = get_provider(QC_PROVIDER)
    if not credentials:
        return {"qc_status": "not_configured", "consistency_score": None, "qc": {"message": f"Provider {QC_PROVIDER} chưa cấu hình."}}

    video_path = _flow_video_path(job.get("result_url"))
    if not video_path:
        return {"qc_status": "error", "consistency_score": None, "qc": {"error_code": "QC_VIDEO_NOT_FOUND", "error": "Không tìm thấy video local để Vision QC."}}

    qc_job_id = f"film_qc_{job['id']}"
    expected_speech = _expected_speech_lines(scene)
    audio_required = bool(expected_speech)
    audio_metrics = _audio_metrics(video_path)
    speech_check = None
    speaker_identity = None
    if audio_required and QC_STT_ENABLED and audio_metrics.get("present") and audio_metrics.get("non_silent"):
        speech_check = await _transcribe_speech(video_path, expected_speech)
        if isinstance(speech_check, dict) and speech_check.get("available"):
            speaker_identity = await asyncio.to_thread(
                verify_or_enroll_scene_speaker,
                project,
                scene,
                video_path,
                speech_check,
            )
            speech_check["speaker_identity"] = speaker_identity
            if speaker_identity.get("speaker_character_id"):
                speech_check["speaker_character_id"] = speaker_identity.get("speaker_character_id")
            if speaker_identity.get("speaker_similarity") is not None:
                speech_check["speaker_similarity"] = speaker_identity.get("speaker_similarity")
            speech_check["speaker_verification"] = speaker_identity
    try:
        keyframes = extract_keyframes(str(video_path), qc_job_id, float(scene.get("duration") or 0))
        video_images = load_frame_payloads(qc_job_id, keyframes)
        if not video_images:
            raise RuntimeError("Không đọc được keyframe payloads.")

        reference_images, reference_evidence = _visual_reference_payloads(project, scene, job)
        manifest = scene_resource_manifest(project, scene, "flow")
        expected_reference_count = len(manifest.get("references") or [])
        actual_canonical_count = len(reference_evidence.get("canonical") or [])
        if expected_reference_count and actual_canonical_count < expected_reference_count:
            raise RuntimeError(
                f"VISUAL_REFERENCE_EVIDENCE_MISSING: chỉ đọc được {actual_canonical_count}/{expected_reference_count} canonical assets."
            )
        images = reference_images + video_images

        context = _expected_context(project, scene, job)
        context["visual_reference_evidence"] = reference_evidence
        required_dimensions = _required_visual_dimensions(scene, job)
        prompt = _qc_prompt(context, audio_required, audio_metrics, required_dimensions)
        candidates = []
        if QC_MODEL:
            candidates.append(QC_MODEL)
        for model in FREE_VISION_FALLBACKS.get(QC_PROVIDER, []):
            if model not in candidates:
                candidates.append(model)
        if not candidates:
            raise RuntimeError(f"Chưa cấu hình vision model cho provider {QC_PROVIDER}.")

        errors = []
        for model in candidates:
            try:
                text = await run_vision(
                    QC_PROVIDER,
                    credentials["api_key"],
                    credentials.get("base_url"),
                    model,
                    prompt,
                    images,
                )
                data = _json_from_text(text)
                data = _apply_visual_hard_gates(data, scene, job)
                data["provider"] = QC_PROVIDER
                data["model"] = model
                data["keyframes"] = keyframes
                data["visual_reference_evidence"] = reference_evidence
                data["audio_check"] = {"required": audio_required, **audio_metrics}
                if speech_check is not None:
                    data["speech_check"] = speech_check
                if audio_required and (not audio_metrics.get("present") or not audio_metrics.get("non_silent")):
                    data["passed"] = False
                    data["consistency_score"] = min(float(data["consistency_score"]), QC_MIN_SCORE - 1)
                    data.setdefault("issues", []).append({
                        "type": "audio",
                        "severity": "critical",
                        "evidence": "Video thiếu audio stream hoặc audio gần như im lặng.",
                        "expected": "Scene có dialogue/voiceover nên cần audio non-silent.",
                    })
                if audio_required and QC_STT_ENABLED and QC_STT_REQUIRE_MATCH:
                    if speech_check is None or not speech_check.get("available"):
                        data["passed"] = False
                        data["consistency_score"] = min(float(data["consistency_score"]), QC_MIN_SCORE - 1)
                        data.setdefault("issues", []).append({
                            "type": "audio",
                            "severity": "high",
                            "evidence": "Không thể chạy STT local để xác minh lời thoại.",
                            "expected": "Scene có dialogue/voiceover nên cần STT verification.",
                        })
                    elif not speech_check.get("passed"):
                        data["passed"] = False
                        data["consistency_score"] = min(float(data["consistency_score"]), QC_MIN_SCORE - 1)
                        data.setdefault("issues", []).append({
                            "type": "audio",
                            "severity": "critical",
                            "evidence": f"Transcript không khớp đủ lời thoại expected: {speech_check.get('transcript') or ''}",
                            "expected": " | ".join(expected_speech),
                        })

                if isinstance(speaker_identity, dict):
                    speaker_status = str(speaker_identity.get("status") or "")
                    if speaker_status == "failed":
                        data["passed"] = False
                        data["consistency_score"] = min(float(data["consistency_score"]), QC_MIN_SCORE - 1)
                        data.setdefault("issues", []).append({
                            "type": "voice_identity",
                            "severity": "critical",
                            "evidence": (
                                f"Speaker similarity={speaker_identity.get('speaker_similarity_percent')}% "
                                f"thấp hơn ngưỡng {speaker_identity.get('threshold')}"
                            ),
                            "expected": "Giọng nói phải khớp acoustic reference của đúng character.",
                        })
                    elif SPEAKER_REQUIRED and speaker_status == "error":
                        data["passed"] = False
                        data["consistency_score"] = min(float(data["consistency_score"]), QC_MIN_SCORE - 1)
                        data.setdefault("issues", []).append({
                            "type": "voice_identity",
                            "severity": "high",
                            "evidence": str(speaker_identity.get("error") or "Speaker verification error"),
                            "expected": "Speaker verification phải chạy được khi scene có dialogue.",
                        })
                score = float(data["consistency_score"])
                return {
                    "qc_status": "passed" if bool(data.get("passed")) and score >= QC_MIN_SCORE else "failed",
                    "consistency_score": score,
                    "qc": data,
                }
            except Exception as exc:
                errors.append(f"{model}: {str(exc)[:500]}")
        return {
            "qc_status": "error",
            "consistency_score": None,
            "qc": {"error_code": "VISION_MODELS_FAILED", "provider": QC_PROVIDER, "errors": errors},
        }
    except Exception as exc:
        return {
            "qc_status": "error",
            "consistency_score": None,
            "qc": {"error_code": "VISION_QC_ERROR", "error": str(exc)[:1600]},
        }
    finally:
        delete_video_artifacts(qc_job_id)


async def _run_external_qc(job: dict, project: dict, scene: dict) -> dict:
    payload = {
        "project_id": project["id"],
        "scene_id": scene["id"],
        "video_url": job.get("result_url"),
        "flow_prompt": scene.get("flow_prompt"),
        "characters": project.get("characters") or [],
        "locations": project.get("locations") or [],
        "visual_style": project.get("visual_style"),
        "start_state": scene.get("start_state"),
        "end_state": scene.get("end_state"),
        "reference": job.get("reference") or {},
    }
    headers = {"Content-Type": "application/json"}
    if QC_KEY:
        headers["Authorization"] = f"Bearer {QC_KEY}"
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("FILM_QC_TIMEOUT", "180"))) as client:
            response = await client.post(QC_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        score = data.get("consistency_score", data.get("score"))
        score = float(score) if score is not None else None
        if score is not None:
            data["consistency_score"] = score
        if "passed" not in data:
            data["passed"] = score is not None and score >= QC_MIN_SCORE
        data = _apply_visual_hard_gates(data, scene, job)
        score = float(data.get("consistency_score")) if data.get("consistency_score") is not None else None
        passed = bool(data.get("passed")) and score is not None and score >= QC_MIN_SCORE
        return {"qc_status": "passed" if passed else "failed", "consistency_score": score, "qc": data}
    except Exception as exc:
        return {"qc_status": "error", "consistency_score": None, "qc": {"error": str(exc)[:1600]}}


async def run_render_qc(job: dict, project: dict, scene: dict) -> dict:
    if QC_URL:
        return await _run_external_qc(job, project, scene)
    return await _run_builtin_qc(job, project, scene)


def run_junction_vision(project: dict, previous_scene: dict, next_scene: dict, evidence: dict) -> dict:
    from .film_junction_vision import run_junction_vision as _run
    return _run(project, previous_scene, next_scene, evidence)
