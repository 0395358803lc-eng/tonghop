from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .film_qc_service import (
    QC_MODEL,
    QC_PROVIDER,
    _image_payload,
    _json_from_text,
    _provider_configured,
    get_qc_status,
)
from .provider_store import get_provider
from .providers.vision import run_vision
from .vision_service import FREE_VISION_FALLBACKS

JUNCTION_VISION_DIMS = (
    "identity",
    "wardrobe",
    "character_position",
    "body_orientation",
    "prop_holder",
    "prop_state",
    "location_geometry",
    "lighting",
    "camera_direction",
    "motion_direction",
)


def _json_object(text: str) -> dict:
    try:
        return _json_from_text(text)
    except Exception:
        raw = (text or "").strip()
        ticks = chr(96) * 3
        if raw.startswith(ticks):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith(ticks):
                raw = raw[: -len(ticks)]
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Vision junction không trả JSON object.")
        data = json.loads(raw[start:end + 1])
        if not isinstance(data, dict):
            raise ValueError("Vision junction JSON không hợp lệ.")
        return data


def _normalize_obs(data: dict) -> dict:
    out = {}
    source = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else data
    for name in JUNCTION_VISION_DIMS:
        item = source.get(name)
        if isinstance(item, dict):
            score = item.get("score")
            try:
                score = float(score) if score is not None else None
            except Exception:
                score = None
            out[name] = {
                "score": score,
                "passed": item.get("passed") if isinstance(item.get("passed"), bool) else (score is not None and score >= 80),
                "evidence": item.get("evidence") or item.get("detail"),
            }
        elif item is not None:
            try:
                score = float(item)
            except Exception:
                score = None
            out[name] = {"score": score, "passed": score is not None and score >= 80, "evidence": None}
    out["observed_summary"] = data.get("observed_summary") or data.get("summary")
    out["provider"] = data.get("provider")
    out["model"] = data.get("model")
    out["vision_raw_summary"] = data.get("observed_summary")
    return out


def _prompt(project: dict, previous_scene: dict, next_scene: dict) -> str:
    context = {
        "previous_scene": {
            "id": previous_scene.get("id"),
            "characters": previous_scene.get("characters"),
            "location_id": previous_scene.get("location_id"),
            "props_present": previous_scene.get("props_present"),
            "end_state": previous_scene.get("end_state"),
            "camera": previous_scene.get("camera"),
            "lighting": previous_scene.get("lighting"),
        },
        "next_scene": {
            "id": next_scene.get("id"),
            "characters": next_scene.get("characters"),
            "location_id": next_scene.get("location_id"),
            "props_present": next_scene.get("props_present"),
            "start_state": next_scene.get("start_state"),
            "camera": next_scene.get("camera"),
            "lighting": next_scene.get("lighting"),
        },
        "characters": (project or {}).get("characters") or [],
        "locations": (project or {}).get("locations") or [],
        "props": (project or {}).get("props") or [],
    }
    return f"""Bạn là Junction QC Vision. Ảnh 1 là LAST FRAME đã duyệt của cảnh trước. Ảnh 2 là FIRST FRAME của cảnh sau.
So sánh TRỰC TIẾP hai khung hình. Không bịa. Không PASS khi không nhìn thấy.
Chấm 0-100 cho từng dimension:
identity, wardrobe, character_position, body_orientation, prop_holder, prop_state, location_geometry, lighting, camera_direction, motion_direction.

CONTEXT:
{json.dumps(context, ensure_ascii=False)}

Chỉ trả JSON object, không markdown:
{{
  "identity": {{"score": 0, "passed": false, "evidence": "..."}},
  "wardrobe": {{"score": 0, "passed": false, "evidence": "..."}},
  "character_position": {{"score": 0, "passed": false, "evidence": "..."}},
  "body_orientation": {{"score": 0, "passed": false, "evidence": "..."}},
  "prop_holder": {{"score": 0, "passed": false, "evidence": "..."}},
  "prop_state": {{"score": 0, "passed": false, "evidence": "..."}},
  "location_geometry": {{"score": 0, "passed": false, "evidence": "..."}},
  "lighting": {{"score": 0, "passed": false, "evidence": "..."}},
  "camera_direction": {{"score": 0, "passed": false, "evidence": "..."}},
  "motion_direction": {{"score": 0, "passed": false, "evidence": "..."}},
  "observed_summary": "..."
}}"""


async def _run_junction_vision(project: dict, previous_scene: dict, next_scene: dict, evidence: dict) -> dict:
    if not _provider_configured():
        raise RuntimeError("JUNCTION_VISION_NOT_CONFIGURED")
    credentials = get_provider(QC_PROVIDER)
    if not credentials:
        raise RuntimeError("JUNCTION_VISION_NOT_CONFIGURED")
    prev = _image_payload(Path(evidence["previous_last_frame_path"]), "PREVIOUS SCENE ACCEPTED LAST FRAME")
    nxt = _image_payload(Path(evidence["next_first_frame_path"]), "NEXT SCENE ACCEPTED FIRST FRAME")
    if not prev or not nxt:
        raise RuntimeError("JUNCTION_EVIDENCE_MISSING")
    prompt = _prompt(project, previous_scene, next_scene)
    candidates = []
    if QC_MODEL:
        candidates.append(QC_MODEL)
    for model in FREE_VISION_FALLBACKS.get(QC_PROVIDER, []):
        if model not in candidates:
            candidates.append(model)
    if not candidates:
        raise RuntimeError("JUNCTION_VISION_MODEL_MISSING")
    errors = []
    for model in candidates:
        try:
            text = await run_vision(
                QC_PROVIDER,
                credentials["api_key"],
                credentials.get("base_url"),
                model,
                prompt,
                [prev, nxt],
            )
            data = _json_object(text)
            data["provider"] = QC_PROVIDER
            data["model"] = model
            return _normalize_obs(data)
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:400]}")
    raise RuntimeError("JUNCTION_VISION_FAILED: " + " | ".join(errors)[:1200])


async def run_junction_vision_async(project: dict, previous_scene: dict, next_scene: dict, evidence: dict) -> dict:
    """Production async entry point. Safe to await from FastAPI/pipeline event loops."""
    return await _run_junction_vision(project, previous_scene, next_scene, evidence)


def run_junction_vision(project: dict, previous_scene: dict, next_scene: dict, evidence: dict) -> dict:
    """Sync compatibility wrapper for CLI/unit callers only."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_junction_vision_async(project, previous_scene, next_scene, evidence))
    raise RuntimeError("JUNCTION_VISION_LOOP: sync wrapper cannot run inside an active event loop; await run_junction_vision_async().")


def junction_qc_status() -> dict:
    status = get_qc_status()
    status["junction"] = True
    return status
