from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from .db import connect
from .film_event_store import emit_event

PROVIDER = "flow"
STALE_AFTER = timedelta(hours=12)
NOISE_MODEL_RE = re.compile(r"(crop[_\-]?\d|\sx\d+\b|\[Lower Priority\])", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> dict | None:
    if not row:
        return None
    data = dict(row)
    for key in ("resolutions_json", "durations_json", "aspect_ratios_json", "raw_json"):
        dest = key[:-5]
        try:
            data[dest] = json.loads(data.pop(key) or ("[]" if key != "raw_json" else "{}"))
        except Exception:
            data[dest] = {} if dest == "raw" else []
    data["supports_image_reference"] = bool(data.get("supports_image_reference"))
    data["supports_audio"] = bool(data.get("supports_audio"))
    return data


def canonical_model_name(label: str) -> str | None:
    text = str(label or "").strip()
    if not text:
        return None
    if NOISE_MODEL_RE.search(text) and "Lite" in text and "crop" in text.lower():
        return None
    cleaned = re.sub(r"\s*\[Lower Priority\]\s*", "", text).strip()
    return cleaned or None


def list_capability_matrix(provider: str = PROVIDER, media_type: str | None = None) -> list[dict]:
    sql = "SELECT * FROM film_capability_matrix WHERE provider=?"
    args: list = [provider]
    if media_type:
        sql += " AND media_type=?"
        args.append(media_type)
    sql += " ORDER BY media_type, model"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row(row) for row in rows]


def upsert_capability(entry: dict) -> dict:
    now = _now()
    item_id = entry.get("id") or str(uuid.uuid4())
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM film_capability_matrix WHERE provider=? AND model=? AND media_type=?",
            (entry["provider"], entry["model"], entry["media_type"]),
        ).fetchone()
        if existing:
            item_id = existing["id"]
            conn.execute(
                """UPDATE film_capability_matrix SET max_references=?, resolutions_json=?, durations_json=?,
                   aspect_ratios_json=?, supports_image_reference=?, supports_audio=?, raw_json=?, checked_at=?
                   WHERE id=?""",
                (
                    entry.get("max_references"),
                    json.dumps(entry.get("resolutions") or [], ensure_ascii=False),
                    json.dumps(entry.get("durations") or [], ensure_ascii=False),
                    json.dumps(entry.get("aspect_ratios") or [], ensure_ascii=False),
                    1 if entry.get("supports_image_reference", True) else 0,
                    1 if entry.get("supports_audio", True) else 0,
                    json.dumps(entry.get("raw") or {}, ensure_ascii=False),
                    now,
                    item_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO film_capability_matrix(
                     id,provider,model,media_type,max_references,resolutions_json,durations_json,
                     aspect_ratios_json,supports_image_reference,supports_audio,raw_json,checked_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    entry["provider"],
                    entry["model"],
                    entry["media_type"],
                    entry.get("max_references"),
                    json.dumps(entry.get("resolutions") or [], ensure_ascii=False),
                    json.dumps(entry.get("durations") or [], ensure_ascii=False),
                    json.dumps(entry.get("aspect_ratios") or [], ensure_ascii=False),
                    1 if entry.get("supports_image_reference", True) else 0,
                    1 if entry.get("supports_audio", True) else 0,
                    json.dumps(entry.get("raw") or {}, ensure_ascii=False),
                    now,
                ),
            )
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_capability_matrix WHERE id=?", (item_id,)).fetchone()
    return _row(row)


def ingest_capability_payload(media_type: str, payload: dict, *, max_references: int | None = None) -> list[dict]:
    models: list[str] = []
    for raw_name in (payload.get("models") or []):
        name = canonical_model_name(raw_name)
        if name and name not in models:
            models.append(name)
    if not models:
        models = ["unknown"]
    saved = []
    shared = {
        "provider": PROVIDER,
        "media_type": media_type,
        "max_references": max_references if max_references is not None else payload.get("max_references") or (6 if media_type == "video" else 1),
        "resolutions": list(payload.get("resolutions") or []),
        "durations": list(payload.get("durations") or []),
        "aspect_ratios": list(payload.get("aspect_ratios") or []),
        "supports_image_reference": True,
        "supports_audio": media_type == "video",
        "raw": payload,
    }
    for model in models:
        saved.append(upsert_capability({**shared, "model": model}))
    emit_event("system", "CAPABILITY_REFRESHED", payload={"media_type": media_type, "models": models, "count": len(saved)})
    return saved


def _payload_models(payload: dict | None) -> list[str]:
    return [str(item).strip() for item in ((payload or {}).get("models") or []) if str(item).strip()]


def _payload_has_preferred_model(payload: dict | None, preferred_model: str | None) -> bool:
    models = _payload_models(payload)
    if not models:
        return False
    preferred = str(preferred_model or "").strip()
    if not preferred:
        return True
    preferred_canonical = canonical_model_name(preferred) or preferred
    canonical = {canonical_model_name(item) or item for item in models}
    return preferred in models or preferred_canonical in canonical


def _replace_capability_rows(media_type: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM film_capability_matrix WHERE provider=? AND media_type=?",
            (PROVIDER, media_type),
        )


async def refresh_capability_matrix(project_id: str | None = None) -> dict:
    from .flow_bridge_client import (
        get_flow_image_capabilities,
        get_flow_projects,
        get_flow_video_capabilities,
    )

    flow_project_id = None
    preferred_model = None
    project = None
    if project_id:
        try:
            from .film_store import get_film_project
            project = get_film_project(project_id)
            settings = (project or {}).get("settings") or {}
            flow_project_id = settings.get("flow_project_id")
            preferred_model = settings.get("flow_model")
        except Exception:
            project = None
            flow_project_id = None
            preferred_model = None

    chosen_id: str | None = None
    chosen_video: dict | None = None
    fallback_id: str | None = None
    fallback_video: dict | None = None

    async def _probe_video(target_flow_project_id: str | None) -> dict | None:
        try:
            payload = await get_flow_video_capabilities(target_flow_project_id)
        except Exception:
            return None
        payload = payload or {}
        if not _payload_models(payload):
            return None
        return payload

    # Fast path: keep the stored binding only when it exposes real video models
    # and, when configured, the requested model family.
    if flow_project_id:
        payload = await _probe_video(flow_project_id)
        if payload:
            fallback_id = str(payload.get("project_id") or flow_project_id)
            fallback_video = payload
            if _payload_has_preferred_model(payload, preferred_model):
                chosen_id = fallback_id
                chosen_video = payload

    # A Flow project can remain reachable but stop exposing the requested model.
    # Treat that as a stale binding, not as a fresh "unknown" capability.
    if chosen_video is None:
        try:
            projects_payload = await get_flow_projects()
            projects = projects_payload.get("projects") or []
        except Exception:
            projects = []
        seen = {str(flow_project_id)} if flow_project_id else set()
        for item in projects:
            candidate_id = str((item or {}).get("id") or "").strip()
            if not candidate_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            payload = await _probe_video(candidate_id)
            if not payload:
                continue
            effective_id = str(payload.get("project_id") or candidate_id)
            if fallback_video is None:
                fallback_id = effective_id
                fallback_video = payload
            if _payload_has_preferred_model(payload, preferred_model):
                chosen_id = effective_id
                chosen_video = payload
                break

    # Last-resort bridge default, useful when project enumeration is temporarily
    # unavailable. It still must expose at least one real model.
    if chosen_video is None and fallback_video is None:
        payload = await _probe_video(None)
        if payload:
            fallback_id = str(payload.get("project_id") or "") or None
            fallback_video = payload

    if chosen_video is None:
        chosen_id = fallback_id
        chosen_video = fallback_video

    if not chosen_video or not chosen_id:
        raise RuntimeError(
            "CAPABILITY_EMPTY_VIDEO_MODELS: Không tìm thấy Flow workspace nào có video model khả dụng."
        )

    image = await get_flow_image_capabilities(chosen_id)
    image = image or {}

    if project and chosen_id != flow_project_id:
        from .film_store import update_film_project
        merged = dict((project or {}).get("settings") or {})
        merged["flow_project_id"] = chosen_id
        update_film_project(project_id, settings=merged)
        flow_project_id = chosen_id

    # Refresh is replacement semantics. Keeping a fresh "unknown" row from a
    # stale workspace can otherwise make readiness incorrectly report fresh.
    _replace_capability_rows("video")
    _replace_capability_rows("image")
    video_rows = ingest_capability_payload(
        "video",
        chosen_video,
        max_references=chosen_video.get("max_references") or 6,
    )
    image_rows = ingest_capability_payload("image", image, max_references=1)
    return {
        "provider": PROVIDER,
        "checked_at": _now(),
        "flow_project_id": flow_project_id or chosen_id,
        "video": video_rows,
        "image": image_rows,
        "stale": False,
    }


def matrix_is_fresh(media_type: str = "video") -> bool:
    rows = [
        row
        for row in list_capability_matrix(media_type=media_type)
        if str(row.get("model") or "").strip().lower() != "unknown"
    ]
    if not rows:
        return False
    stamps = [stamp for stamp in (_parse(row.get("checked_at")) for row in rows) if stamp]
    if not stamps:
        return False
    latest = max(stamps)
    return datetime.now(timezone.utc) - latest < STALE_AFTER


def _parse(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def evaluate_capability(
    *,
    model: str | None,
    media_type: str = "video",
    duration: float | int | None = None,
    resolution: str | None = None,
    reference_count: int | None = None,
    aspect_ratio: str | None = None,
) -> dict:
    rows = list_capability_matrix(media_type=media_type)
    if not rows:
        return {"ok": False, "code": "CAPABILITY_MATRIX_MISSING", "detail": "Capability matrix chưa được refresh.", "blocked": True}
    wanted = canonical_model_name(model or "") or (model or "")
    match = next((row for row in rows if row.get("model") == wanted), None)
    if wanted and not match:
        return {"ok": False, "code": "CAPABILITY_MODEL_UNSUPPORTED", "detail": f"Model {wanted} không có trong capability matrix.", "blocked": True, "model": wanted}
    target = match or rows[0]
    errors = []
    max_refs = target.get("max_references")
    if reference_count is not None and max_refs is not None and int(reference_count) > int(max_refs):
        errors.append({"code": "CAPABILITY_REF_LIMIT", "detail": f"required refs {reference_count} > max_references {max_refs}"})
    durations = [float(x) for x in (target.get("durations") or [])]
    if duration is not None and durations and float(duration) not in durations:
        errors.append({"code": "CAPABILITY_DURATION_UNSUPPORTED", "detail": f"duration {duration} không nằm trong {durations}"})
    resolutions = [str(x) for x in (target.get("resolutions") or [])]
    if resolution and resolutions and str(resolution) not in resolutions:
        errors.append({"code": "CAPABILITY_RESOLUTION_UNSUPPORTED", "detail": f"resolution {resolution} không nằm trong {resolutions}"})
    ratios = [str(x) for x in (target.get("aspect_ratios") or [])]
    if aspect_ratio and ratios and str(aspect_ratio) not in ratios:
        errors.append({"code": "CAPABILITY_ASPECT_UNSUPPORTED", "detail": f"aspect_ratio {aspect_ratio} không nằm trong {ratios}"})
    if errors:
        emit_event("system", "CAPABILITY_BLOCKED", severity="ERROR", payload={"model": target.get("model"), "errors": errors})
        return {"ok": False, "blocked": True, "code": errors[0]["code"], "errors": errors, "model": target.get("model"), "capability": target}
    return {"ok": True, "blocked": False, "model": target.get("model"), "capability": target, "fresh": matrix_is_fresh(media_type)}


def select_model(
    *,
    media_type: str = "video",
    reference_count: int | None = None,
    duration: float | int | None = None,
    resolution: str | None = None,
    aspect_ratio: str | None = None,
    preferred: str | None = None,
    fallback_models: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Select only from an explicit ordered policy; never from incidental DB row order."""
    rows = list_capability_matrix(media_type=media_type)
    if not rows:
        return {"ok": False, "blocked": True, "code": "CAPABILITY_MATRIX_MISSING"}

    by_name = {str(row.get("model")): row for row in rows}
    candidates: list[str] = []

    def _add(name: str | None):
        if not name:
            return
        canonical = canonical_model_name(name) or str(name)
        if canonical not in candidates:
            candidates.append(canonical)

    _add(preferred)
    for name in (fallback_models or ()):
        _add(name)

    # Without an explicit preferred/fallback policy, evaluate in stable canonical-name
    # order. Production pipeline currently uses evaluate_capability() directly and
    # therefore does not silently auto-switch models.
    if not candidates:
        candidates = sorted(by_name)

    for name in candidates:
        row = by_name.get(name)
        if not row:
            continue
        report = evaluate_capability(
            model=row.get("model"),
            media_type=media_type,
            duration=duration,
            resolution=resolution,
            reference_count=reference_count,
            aspect_ratio=aspect_ratio,
        )
        if report.get("ok"):
            report["selected"] = row.get("model")
            preferred_name = canonical_model_name(preferred or "") or preferred
            report["auto"] = bool(preferred) and row.get("model") != preferred_name
            report["selection_policy"] = list(candidates)
            return report
    return {
        "ok": False,
        "blocked": True,
        "code": "CAPABILITY_NO_MODEL",
        "detail": "Không có model nào trong explicit selection policy thỏa reference/duration/resolution.",
        "selection_policy": list(candidates),
    }
