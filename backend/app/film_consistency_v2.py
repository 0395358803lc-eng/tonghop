from __future__ import annotations

import asyncio
import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timezone

from .film_production_gate import auto_repair_project_derived, evaluate_project_production_gate, run_production_gate
from .film_store import (
    append_repair_log,
    get_film_project,
    save_consistency_report,
    save_consistency_state_normalization,
    update_film_status,
)
from .provider_store import get_provider
from .providers.models import list_models
from .providers.service import run_chat


AI_TIMEOUT = int(os.getenv("FILM_CONSISTENCY_AI_TIMEOUT", "180"))
AI_BATCH_SCENES = max(4, int(os.getenv("FILM_CONSISTENCY_AI_BATCH_SCENES", "20")))
AI_MIN_CONFIRM = float(os.getenv("FILM_CONSISTENCY_AI_MIN_CONFIRM", "0.85"))
AI_MIN_FALSE_POSITIVE = float(os.getenv("FILM_CONSISTENCY_AI_MIN_FALSE_POSITIVE", "0.92"))
AI_RETRY_ATTEMPTS = max(1, min(int(os.getenv("FILM_CONSISTENCY_AI_RETRY_ATTEMPTS", "2")), 3))
AI_RETRY_BASE_SECONDS = max(1.0, float(os.getenv("FILM_CONSISTENCY_AI_RETRY_BASE_SECONDS", "5")))
AI_FALLBACK_MODELS = [
    x.strip() for x in os.getenv(
        "FILM_CONSISTENCY_FALLBACK_MODELS",
        "qwen/qwen3.5-plus:free,qwen/qwen3.7-flash:free,minimax/minimax-m3:free,qwen/qwen3.6-plus:free",
    ).split(",") if x.strip()
]
AI_RATE_LIMIT_COOLDOWN_SECONDS = max(
    30,
    int(os.getenv("FILM_CONSISTENCY_AI_RATE_LIMIT_COOLDOWN_SECONDS", "120")),
)
_AI_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}

HARD_RULE_CODES = {
    "SOURCE_LOCK_FAILED", "SOURCE_HASH_INVALID", "SOURCE_SNAPSHOT_MISSING",
    "UNKNOWN_CHARACTER", "UNKNOWN_LOCATION", "UNKNOWN_PROP", "UNKNOWN_PROP_PRESENT",
    "UNKNOWN_PROP_TRANSFER", "UNKNOWN_PROP_OWNER", "PROP_OWNER_MISMATCH",
    "DUPLICATE_PROP_TRANSFER", "DUPLICATE_EVENT", "DUPLICATE_SOURCE_BEAT",
    "DIALOGUE_UNKNOWN_SPEAKER", "DIALOGUE_SPEAKER_OFFSCREEN",
    "DIALOGUE_NOT_IN_SOURCE_EXCERPT", "VOICEOVER_NOT_IN_SOURCE_EXCERPT",
    "TIMELINE_SOURCE_REORDERED",
}

SEMANTIC_CONFLICT_CODES = {
    "INVALID_PROP_STATE_TRANSITION", "LOCATION_TELEPORT", "WEATHER_REGRESSION",
    "START_END_PROP_STATE_MISMATCH", "START_END_PROP_OWNER_MISMATCH",
}

SAFE_PATCH_TYPES = {
    "REBUILD_STRUCTURED_STATE", "NORMALIZE_PROP_STATE", "RELINK_CONTINUITY",
    "RECOMPILE_PROMPT", "REBUILD_SHOT_PLAN", "CLEAN_WARNINGS",
    "REBUILD_SOURCE_SPAN", "NONE",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_from_text(text: str) -> dict:
    value = str(text or "").strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        value = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*" + re.escape(fence) + r"$", "", value)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(value[start:end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("AI semantic reviewer không trả về JSON hợp lệ")


def _canonical_scene_id(value: object, project: dict) -> str:
    raw = str(value or "").strip().upper()
    if not raw or raw == "PROJECT":
        return raw or "PROJECT"

    actual_ids = [str(scene.get("id") or "").strip().upper() for scene in (project.get("scenes") or [])]
    if raw in actual_ids:
        return raw

    match = re.fullmatch(r"SCENE_(\d+)", raw)
    if not match:
        return raw

    number = int(match.group(1))
    numeric_matches = []
    for scene_id in actual_ids:
        scene_match = re.fullmatch(r"SCENE_(\d+)", scene_id)
        if scene_match and int(scene_match.group(1)) == number:
            numeric_matches.append(scene_id)
    return numeric_matches[0] if len(numeric_matches) == 1 else raw


def _scene_payload(scene: dict) -> dict:
    return {
        "id": scene.get("id"),
        "duration": scene.get("duration"),
        "characters": scene.get("characters") or [],
        "location_id": scene.get("location_id"),
        "props_present": scene.get("props_present") or [],
        "prop_transfers": scene.get("prop_transfers") or [],
        "source_text": scene.get("source_text") or "",
        "action": scene.get("action") or "",
        "dialogue": scene.get("dialogue") or [],
        "voiceover": scene.get("voiceover") or "",
        "start_state": scene.get("start_state") or "",
        "end_state": scene.get("end_state") or "",
        "continuity": scene.get("continuity") or {},
        "start_state_structured": scene.get("start_state_structured") or {},
        "end_state_structured": scene.get("end_state_structured") or {},
    }


def _compact_report(report: dict) -> dict:
    return {
        "version": report.get("version"),
        "final_gate": report.get("final_gate"),
        "gates": report.get("gates") or {},
        "errors": report.get("errors") or [],
        "warnings": report.get("warnings") or [],
        "prop_ledger": report.get("prop_ledger") or [],
        "event_ledger": report.get("event_ledger") or [],
    }


def _build_messages(project: dict, deterministic_report: dict, scenes: list[dict], batch_label: str) -> list[dict]:
    payload = {
        "project": {
            "id": project.get("id"),
            "name": project.get("name"),
            "total_scenes": len(project.get("scenes") or []),
            "total_duration": sum(float(x.get("duration") or 0) for x in (project.get("scenes") or [])),
        },
        "story_bible": project.get("story_bible") or {},
        "characters": project.get("characters") or [],
        "locations": project.get("locations") or [],
        "props": project.get("props") or [],
        "timeline": project.get("timeline") or [],
        "deterministic_report": _compact_report(deterministic_report),
        "review_batch": batch_label,
        "scenes": [_scene_payload(x) for x in scenes],
    }
    system = """
Bạn là TH Media Consistency Semantic Reviewer V2.
Bạn KHÔNG phải biên kịch và KHÔNG được viết lại kịch bản.

Nhiệm vụ:
1. Đọc Bible, timeline, Prop/Event Ledger và các scene được cung cấp.
2. Kiểm tra continuity ngữ nghĩa xuyên scene: nhân vật, bối cảnh, đạo cụ, owner/state,
   event order, movement, dialogue speaker, START/END, duration và quan hệ nguyên nhân-kết quả.
3. Đối chiếu lỗi deterministic với evidence thực tế. Có thể xác nhận lỗi, phát hiện false-positive,
   hoặc đánh dấu cần review.
4. Với prop thiết bị, hiểu state đa chiều độc lập: lid_state=open có thể đồng thời playback_state=playing.
5. Prop Ledger và PROP_TRANSFERS là nguồn có thẩm quyền cho owner. owner=null trong START/END structured state
   chỉ có nghĩa scene không nhắc lại owner. Đặt một prop lên bàn/kệ/mâm KHÔNG tự đổi owner nếu không có PROP_TRANSFER mới.
6. Field "state" trong Prop Ledger chỉ là scalar legacy để tương thích. Khi state_dimensions tồn tại,
   state_dimensions mới là nguồn có thẩm quyền; KHÔNG được báo lỗi chỉ vì state legacy khác một dimension.
7. Action có thể cung cấp state transition cho derived state: đặt đĩa lên mâm => device.disc_state=loaded
   và prop đĩa có quan hệ mounted_on với thiết bị.
   hạ kim => needle_state=down, mâm hoặc đĩa quay => rotation_state=spinning.
   Chỉ khi source nói nhạc/giai điệu đã vang hoặc đang tiếp tục mới dùng playback_state=playing.
   Mâm quay hoặc kim hạ một mình KHÔNG đủ để kết luận playback_state=playing.
   Cụm "not fully revealed", "edge visible", "partially visible" là visibility_state=partial.
8. Prop Ledger chỉ cần row cho scene có prop hiện diện hoặc prop transfer. Scene có PROPS_PRESENT=[]
   không phải lỗi nếu không có ledger row; trạng thái prop ngoài scene được carry-forward ngầm.
9. Cụm phủ định như "no PROP_001", "without PROP_001", "không có PROP_001" hoặc mô tả "PROP_001 left inside"
   trong một scene khác location KHÔNG có nghĩa prop đang hiện diện trong scene. Ưu tiên PROPS_PRESENT và ngữ nghĩa phủ định.
10. Scene ở ngưỡng/junction có thể cố ý dùng location_id của một phía dù nhân vật/bối cảnh của cả hai phía cùng xuất hiện.
    Nếu START/END/source ghi rõ threshold/junction/LOC_A↔LOC_B thì không được tự đổi location_id chỉ vì suy luận không gian.
11. Không đề xuất sửa action/dialogue/voiceover/source nếu có thể sửa representation/derived data.
12. Chỉ dùng evidence có trong input. Không invent event/entity.

Chỉ trả JSON đúng schema:
{
  "verdict": "PASS|FAIL|REVIEW_REQUIRED",
  "summary": "string",
  "issues": [
    {
      "scene_id": "SCENE_001|PROJECT",
      "issue_type": "PROP_STATE|CHARACTER|LOCATION|EVENT_ORDER|START_END|DIALOGUE|DURATION|SOURCE|OTHER",
      "severity": "hard|medium|soft",
      "deterministic_code": "code hoặc null",
      "disposition": "CONFIRMED_ERROR|FALSE_POSITIVE|NEEDS_REVIEW|NEW_ISSUE",
      "entity_id": "PROP_001|CHAR_001|LOC_001|null",
      "dimension": "owner|container|visibility_state|playback_state|rotation_state|disc_state|needle_state|power_state|lid_state|relation.mounted_on|null",
      "actual_value": "giá trị đang có trong ledger hoặc null",
      "expected_value": "giá trị AI cho rằng đúng hoặc null",
      "evidence_scene_ids": ["SCENE_001"],
      "evidence": "bằng chứng cụ thể",
      "reason": "lý do",
      "confidence": 0.0,
      "repairability": "SAFE_DERIVED|SEMANTIC_NORMALIZATION|SOURCE_SENSITIVE|NONE",
      "suggested_patch_type": "REBUILD_STRUCTURED_STATE|NORMALIZE_PROP_STATE|RELINK_CONTINUITY|RECOMPILE_PROMPT|REBUILD_SHOT_PLAN|CLEAN_WARNINGS|REBUILD_SOURCE_SPAN|NONE"
    }
  ]
}
Nếu issue nói về state/owner/prop cụ thể thì BẮT BUỘC khai entity_id, dimension, actual_value, expected_value.
Không được khai actual_value nếu không nhìn thấy nó trong ledger/input.
Nếu không có lỗi ngữ nghĩa, verdict=PASS và issues=[].
"""
    user = "Hãy audit continuity cho dữ liệu sau. Không sửa source.\n" + json.dumps(payload, ensure_ascii=False)
    return [{"role": "system", "content": system.strip()}, {"role": "user", "content": user}]


async def _semantic_model_candidates(project: dict, credentials: dict) -> list[str]:
    requested = str(project.get("model") or "").strip()
    provider = str(project.get("provider") or "").strip()
    try:
        live_models = await list_models(provider, credentials["api_key"], credentials.get("base_url"))
    except Exception:
        live_models = []
    live_set = set(live_models or [])

    candidates = []
    if requested:
        candidates.append(requested)
    for model in AI_FALLBACK_MODELS:
        if not live_set or model in live_set:
            candidates.append(model)

    out = []
    seen = set()
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        out.append(model)
    return out or ([requested] if requested else [])


async def _semantic_review(project: dict, deterministic_report: dict, verification_feedback: list[dict] | None = None) -> dict:
    provider = str(project.get("provider") or "")
    credentials = get_provider(provider)
    requested_model = str(project.get("model") or "")
    cooldown_until = _AI_PROVIDER_COOLDOWN_UNTIL.get(provider, 0.0)
    now = time.time()
    if cooldown_until > now:
        return {
            "available": False,
            "provider": provider,
            "requested_model": requested_model,
            "model": None,
            "verdict": "REVIEW_REQUIRED",
            "summary": "AI Semantic Reviewer đang tạm nghỉ do xKiro/provider vừa trả rate-limit.",
            "issues": [],
            "error": "PROVIDER_RATE_LIMIT_COOLDOWN",
            "status_code": 429,
            "retry_after_seconds": int(max(1, cooldown_until - now)),
        }
    if not credentials:
        return {
            "available": False,
            "requested_model": requested_model,
            "model": requested_model,
            "verdict": "REVIEW_REQUIRED",
            "summary": "Chưa có API key để chạy AI Semantic Reviewer.",
            "issues": [],
            "error": "PROVIDER_NOT_CONFIGURED",
        }

    model_candidates = await _semantic_model_candidates(project, credentials)
    if not model_candidates:
        return {
            "available": False,
            "requested_model": requested_model,
            "model": requested_model,
            "verdict": "REVIEW_REQUIRED",
            "summary": "Không tìm thấy model khả dụng cho AI Semantic Reviewer.",
            "issues": [],
            "error": "NO_REVIEW_MODEL_AVAILABLE",
        }

    scenes = project.get("scenes") or []
    batches = [scenes[i:i + AI_BATCH_SCENES] for i in range(0, len(scenes), AI_BATCH_SCENES)] or [[]]
    all_issues = []
    summaries = []
    batch_verdicts = []
    tried_models = []
    used_models = []
    failover_events = []

    for index, batch in enumerate(batches, start=1):
        label = f"{index}/{len(batches)}"
        messages = _build_messages(project, deterministic_report, batch, label)
        if verification_feedback:
            feedback = [
                {
                    "scene_id": x.get("scene_id"),
                    "entity_id": x.get("entity_id"),
                    "dimension": x.get("dimension"),
                    "claimed_actual": x.get("actual_value"),
                    "verification_note": x.get("verification_note"),
                }
                for x in verification_feedback
            ]
            messages.append({
                "role": "user",
                "content": "Backend Evidence Verifier đã bác các finding sau vì không khớp ledger thật. "
                           "Hãy đọc lại dữ liệu và trả JSON sửa lại; không lặp finding đã bị bác nếu không có evidence mới:\n"
                           + json.dumps(feedback, ensure_ascii=False),
            })

        parsed = None
        last_error = None
        selected_model = None

        for candidate in model_candidates:
            if candidate not in tried_models:
                tried_models.append(candidate)

            for attempt in range(AI_RETRY_ATTEMPTS):
                try:
                    answer = await asyncio.wait_for(
                        run_chat(
                            project["provider"],
                            credentials["api_key"],
                            credentials.get("base_url"),
                            candidate,
                            messages,
                        ),
                        timeout=AI_TIMEOUT,
                    )
                    parsed = _json_from_text(answer)
                    selected_model = candidate
                    break
                except Exception as exc:
                    last_error = exc
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)

                    failover_events.append({
                        "batch": label,
                        "model": candidate,
                        "attempt": attempt + 1,
                        "status_code": status_code,
                        "error_type": type(exc).__name__,
                    })

                    if status_code in {401, 403}:
                        break

                    retriable_same_model = (
                        isinstance(exc, asyncio.TimeoutError)
                        or status_code in {408, 425, 500, 502, 503, 504}
                    )
                    if status_code == 429:
                        # Move immediately to the next free model; retrying the same
                        # rate-limited model only increases latency.
                        retriable_same_model = False

                    if not retriable_same_model or attempt + 1 >= AI_RETRY_ATTEMPTS:
                        break

                    retry_after = None
                    try:
                        retry_after = float((getattr(response, "headers", {}) or {}).get("retry-after"))
                    except Exception:
                        retry_after = None
                    delay = retry_after if retry_after and retry_after > 0 else AI_RETRY_BASE_SECONDS * (2 ** attempt)
                    await asyncio.sleep(min(delay, 20.0))

            if parsed is not None:
                break

            status_code = getattr(getattr(last_error, "response", None), "status_code", None)
            if status_code in {401, 403}:
                break

        if parsed is None:
            status_code = getattr(getattr(last_error, "response", None), "status_code", None)
            all_rate_limited = bool(failover_events) and all(
                event.get("status_code") == 429
                for event in failover_events
                if event.get("batch") == label
            )
            if status_code == 429 or all_rate_limited:
                _AI_PROVIDER_COOLDOWN_UNTIL[provider] = time.time() + AI_RATE_LIMIT_COOLDOWN_SECONDS
            return {
                "available": False,
                "provider": provider,
                "requested_model": requested_model,
                "model": None,
                "verdict": "REVIEW_REQUIRED",
                "summary": (
                    "AI Semantic Reviewer bị giới hạn tốc độ trên toàn bộ model miễn phí."
                    if status_code == 429 or all_rate_limited
                    else f"AI Semantic Reviewer không còn model khả dụng ở batch {label}."
                ),
                "issues": all_issues,
                "error": (
                    "PROVIDER_RATE_LIMIT_ALL_FREE_MODELS"
                    if status_code == 429 or all_rate_limited
                    else (str(last_error)[:500] if last_error else "UNKNOWN_AI_ERROR")
                ),
                "status_code": status_code,
                "retry_after_seconds": AI_RATE_LIMIT_COOLDOWN_SECONDS if status_code == 429 or all_rate_limited else None,
                "tried_models": tried_models,
                "failover_events": failover_events[-20:],
            }

        used_models.append(selected_model)
        verdict = str(parsed.get("verdict") or "REVIEW_REQUIRED").upper()
        batch_verdicts.append(verdict)
        if parsed.get("summary"):
            summaries.append(str(parsed["summary"]))
        for issue in parsed.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            try:
                confidence = float(issue.get("confidence") or 0)
            except Exception:
                confidence = 0.0
            issue = dict(issue)
            issue["scene_id"] = _canonical_scene_id(issue.get("scene_id"), project)
            issue["evidence_scene_ids"] = [
                _canonical_scene_id(scene_id, project)
                for scene_id in (issue.get("evidence_scene_ids") or [])
            ]
            issue["confidence"] = max(0.0, min(confidence, 1.0))
            patch = str(issue.get("suggested_patch_type") or "NONE").upper()
            issue["suggested_patch_type"] = patch if patch in SAFE_PATCH_TYPES else "NONE"
            repairability = str(issue.get("repairability") or "NONE").upper()
            if repairability not in {"SAFE_DERIVED", "SEMANTIC_NORMALIZATION", "SOURCE_SENSITIVE", "NONE"}:
                repairability = "NONE"
            issue["repairability"] = repairability
            issue["review_batch"] = label
            issue["reviewer_model"] = selected_model
            all_issues.append(issue)

    verdict = "PASS"
    if any(v == "FAIL" for v in batch_verdicts):
        verdict = "FAIL"
    elif any(v == "REVIEW_REQUIRED" for v in batch_verdicts):
        verdict = "REVIEW_REQUIRED"

    unique_used = list(dict.fromkeys(x for x in used_models if x))
    model_used = unique_used[0] if len(unique_used) == 1 else ", ".join(unique_used)
    return {
        "available": True,
        "provider": project.get("provider"),
        "requested_model": requested_model,
        "model": model_used,
        "models_used": unique_used,
        "fallback_used": any(x != requested_model for x in unique_used),
        "tried_models": tried_models,
        "failover_events": failover_events[-20:],
        "verdict": verdict,
        "summary": " | ".join(summaries)[:3000],
        "issues": all_issues,
        "batch_count": len(batches),
    }


def _latest_prop_row(deterministic_report: dict, scene_id: str, entity_id: str) -> dict | None:
    rows = [
        row for row in (deterministic_report.get("prop_ledger") or [])
        if str(row.get("scene_id") or "") == str(scene_id)
        and str(row.get("prop_id") or "") == str(entity_id)
    ]
    return rows[-1] if rows else None


def _authoritative_value(row: dict, dimension: str):
    if dimension == "owner":
        return row.get("owner")
    if dimension == "container":
        return row.get("container")
    if dimension == "state":
        return row.get("state")
    if dimension.startswith("relation."):
        return (row.get("relations") or {}).get(dimension.split(".", 1)[1])
    return (row.get("state_dimensions") or {}).get(dimension)


def _verify_semantic_issues(semantic: dict, deterministic_report: dict, project: dict) -> dict:
    verified = []
    rejected = []
    raw = list(semantic.get("issues") or [])
    scene_map = {
        str(scene.get("id") or ""): scene
        for scene in (project.get("scenes") or [])
        if isinstance(scene, dict) and scene.get("id")
    }

    def _explicit_owner_through_scene(prop_id: str, scene_id: str) -> str | None:
        owner = None
        for prop in (project.get("props") or []):
            if str(prop.get("id") or "").upper() == prop_id.upper():
                owner = prop.get("owner_initial") or prop.get("initial_owner")
                break

        for scene in (project.get("scenes") or []):
            sid = str(scene.get("id") or "")
            for transfer in (scene.get("prop_transfers") or []):
                if str(transfer.get("prop_id") or "").upper() != prop_id.upper():
                    continue
                target = transfer.get("target_owner")
                if target:
                    owner = target
            if sid == scene_id:
                break
        return str(owner) if owner is not None else None

    def _scene_has_audible_playback(scene: dict) -> bool:
        text = " ".join([
            str(scene.get("source_text") or ""),
            str(scene.get("action") or ""),
            str(scene.get("start_state") or ""),
            str(scene.get("end_state") or ""),
        ]).lower()
        negative = (
            "not yet audible",
            "music not yet audible",
            "chưa nghe thấy nhạc",
            "nhạc chưa vang",
            "chưa phát nhạc",
        )
        if any(token in text for token in negative):
            return False
        positive = (
            "music playing",
            "music starts",
            "music begins",
            "music audible",
            "giai điệu",
            "nhạc vang",
            "nhạc bắt đầu",
            "nhạc đang phát",
            "âm nhạc",
        )
        return any(token in text for token in positive)

    dimension_values = {
        "visibility_state": {"hidden", "partial", "revealed"},
        "playback_state": {"idle", "playing", "stopped"},
        "rotation_state": {"stopped", "spinning"},
        "disc_state": {"empty", "loaded"},
        "needle_state": {"up", "down"},
        "power_state": {"off", "on"},
        "lid_state": {"open", "closed"},
        "container_state": {"sealed", "open", "closed"},
        "lock_state": {"locked", "unlocked"},
        "mechanical_state": {"sealed", "locked", "unlocked", "open", "closed"},
    }

    for original in raw:
        issue = dict(original)
        entity_id = issue.get("entity_id")
        dimension = issue.get("dimension")
        scene_id = issue.get("scene_id") or "PROJECT"

        if (
            str(entity_id or "").startswith("PROP_")
            and str(issue.get("issue_type") or "").upper() == "LOCATION"
            and not dimension
            and scene_id != "PROJECT"
        ):
            scene = scene_map.get(str(scene_id))
            if scene:
                prop_id = str(entity_id).upper()
                props_present = {str(x).upper() for x in (scene.get("props_present") or []) if x}
                raw_context = " ".join([
                    str(scene.get("start_state") or ""),
                    str(scene.get("end_state") or ""),
                    str(scene.get("action") or ""),
                    str(scene.get("source_text") or ""),
                ])
                escaped = re.escape(prop_id)
                explicit_absence = any(re.search(pattern, raw_context, re.I) for pattern in (
                    rf"\bno\s+(?:the\s+)?{escaped}\b",
                    rf"\bwithout\s+(?:the\s+)?{escaped}\b",
                    rf"\bkhông\s+(?:có|còn|mang|giữ)\s+{escaped}\b",
                ))
                if prop_id not in props_present and explicit_absence:
                    issue["evidence_verified"] = False
                    issue["verification_note"] = (
                        f"{prop_id} được source nhắc trong ngữ cảnh phủ định/off-screen và không có trong props_present; "
                        "không được coi token textual là bằng chứng prop hiện diện sai location."
                    )
                    rejected.append(issue)
                    continue

        if (
            str(entity_id or "").startswith("LOC_")
            and str(issue.get("issue_type") or "").upper() == "LOCATION"
            and not dimension
            and scene_id != "PROJECT"
        ):
            scene = scene_map.get(str(scene_id))
            if scene:
                actual_location = str(issue.get("actual_value") or "")
                expected_location = str(issue.get("expected_value") or "")
                source_location = str(
                    (scene.get("source_snapshot") or {}).get("location_id")
                    or scene.get("location_id")
                    or ""
                )
                boundary_context = " ".join([
                    str(scene.get("action") or ""),
                    str(scene.get("start_state") or ""),
                    str(scene.get("end_state") or ""),
                    str(scene.get("source_text") or ""),
                ])
                boundary_marked = any(token in boundary_context.lower() for token in (
                    "threshold", "junction", "ngưỡng", "cửa vào", "doorway",
                )) or "↔" in boundary_context
                both_locations_named = bool(
                    actual_location
                    and expected_location
                    and actual_location in boundary_context
                    and expected_location in boundary_context
                )
                if (
                    actual_location
                    and source_location == actual_location
                    and expected_location
                    and expected_location != actual_location
                    and boundary_marked
                    and both_locations_named
                ):
                    issue["evidence_verified"] = False
                    issue["verification_note"] = (
                        f"{scene_id} là boundary/junction scene đã source-lock location_id={actual_location}; "
                        f"source đồng thời nhắc {actual_location}↔{expected_location}. "
                        "Không được tự đổi location_id chỉ từ suy luận không gian."
                    )
                    rejected.append(issue)
                    continue

        if str(issue.get("deterministic_code") or "") == "MISSING_LEDGER_ENTRY":
            claim_text = " ".join([
                str(issue.get("actual_value") or ""),
                str(issue.get("reason") or ""),
                str(issue.get("evidence") or ""),
            ])
            claimed_scene_ids = sorted(set(re.findall(r"SCENE_\d+", claim_text, flags=re.I)))
            missing_expected_rows = []
            for claimed_sid in claimed_scene_ids:
                scene = scene_map.get(claimed_sid.upper())
                if not scene:
                    continue
                has_props = bool(scene.get("props_present") or scene.get("prop_transfers"))
                if has_props:
                    rows = [
                        row for row in (deterministic_report.get("prop_ledger") or [])
                        if str(row.get("scene_id") or "") == claimed_sid.upper()
                    ]
                    if not rows:
                        missing_expected_rows.append(claimed_sid.upper())
            if claimed_scene_ids and not missing_expected_rows:
                issue["evidence_verified"] = False
                issue["verification_note"] = (
                    "Prop Ledger chỉ cần row khi scene có prop hiện diện hoặc transfer. "
                    "Các scene AI viện dẫn không thiếu row bắt buộc; scene không có prop được carry-forward ngầm."
                )
                rejected.append(issue)
                continue

        if entity_id and str(entity_id).startswith("PROP_") and dimension:
            row = _latest_prop_row(deterministic_report, str(scene_id), str(entity_id))
            if not row:
                issue["evidence_verified"] = False
                issue["verification_note"] = "Không tìm thấy row tương ứng trong Prop Ledger."
                rejected.append(issue)
                continue

            authoritative = _authoritative_value(row, str(dimension))
            if str(dimension) == "state" and (row.get("state_dimensions") or {}):
                issue["evidence_verified"] = False
                issue["verification_note"] = "Field state là legacy scalar; state_dimensions mới là nguồn có thẩm quyền."
                rejected.append(issue)
                continue

            if "actual_value" in issue:
                claim = issue.get("actual_value")
                if claim != authoritative:
                    issue["evidence_verified"] = False
                    issue["verification_note"] = f"AI khai actual_value={claim!r}, ledger thực tế={authoritative!r}."
                    rejected.append(issue)
                    continue

            expected = issue.get("expected_value")

            if expected is not None and authoritative is not None and expected != authoritative:
                scene = scene_map.get(str(scene_id))
                if scene:
                    source_context = " ".join([
                        str(scene.get("action") or ""),
                        str(scene.get("start_state") or ""),
                        str(scene.get("end_state") or ""),
                        str(scene.get("source_text") or ""),
                    ]).lower()
                    entity_token = str(entity_id or "").lower()
                    explicit_patterns = {
                        ("mechanical_state", "open"): (
                            rf"\bopened\s+{re.escape(entity_token)}\b",
                            rf"\bopen\s+{re.escape(entity_token)}\b",
                            rf"\b{re.escape(entity_token)}\b[^.;]{{0,48}}\bopen\b",
                            r"\bopen box remains\b",
                            r"\bopened box\b",
                        ),
                        ("mechanical_state", "closed"): (
                            rf"\bclosed\s+{re.escape(entity_token)}\b",
                            rf"\b{re.escape(entity_token)}\b[^.;]{{0,48}}\bclosed\b",
                            r"\bcase back closed\b",
                            r"\bđóng nắp\b",
                        ),
                    }
                    patterns = explicit_patterns.get((str(dimension), str(authoritative).lower()), ())
                    if patterns and any(re.search(pattern, source_context, re.I) for pattern in patterns):
                        issue["evidence_verified"] = False
                        issue["verification_note"] = (
                            f"Source của {scene_id} xác nhận {entity_id}.{dimension}={authoritative!r}; "
                            f"ledger cũng là {authoritative!r}. AI không được suy diễn thành {expected!r} "
                            "nếu source không có transition tương ứng."
                        )
                        rejected.append(issue)
                        continue

            if str(dimension) == "owner":
                explicit_owner = _explicit_owner_through_scene(str(entity_id), str(scene_id))
                if (
                    explicit_owner is not None
                    and authoritative == explicit_owner
                    and expected is not None
                    and expected != authoritative
                ):
                    issue["evidence_verified"] = False
                    issue["verification_note"] = (
                        f"PROP_TRANSFERS xác nhận owner={explicit_owner!r} tới {scene_id}; "
                        f"ledger cũng là {authoritative!r}. AI không được suy đoán đổi owner thành {expected!r} "
                        "nếu source không có transfer mới."
                    )
                    rejected.append(issue)
                    continue

            if str(dimension) == "playback_state" and expected == "playing":
                scene = scene_map.get(str(scene_id))
                if scene and not _scene_has_audible_playback(scene):
                    issue["evidence_verified"] = False
                    issue["verification_note"] = (
                        "playback_state=playing cần bằng chứng âm thanh đã thực sự phát trong scene. "
                        "Mâm quay/kim hạ chỉ chứng minh rotation_state/needle_state, không chứng minh playback đã audible."
                    )
                    rejected.append(issue)
                    continue

            allowed_values = dimension_values.get(str(dimension))
            if allowed_values is not None and expected is not None and expected not in allowed_values:
                issue["evidence_verified"] = False
                issue["verification_note"] = (
                    f"expected_value={expected!r} không thuộc domain hợp lệ của {dimension}: "
                    + ", ".join(sorted(allowed_values))
                )
                rejected.append(issue)
                continue

            if (
                expected is not None
                and expected == authoritative
                and str(issue.get("disposition") or "").upper() in {"CONFIRMED_ERROR", "NEW_ISSUE"}
            ):
                issue["evidence_verified"] = False
                issue["verification_note"] = (
                    f"AI báo mâu thuẫn nhưng actual_value và expected_value đều là {authoritative!r}; "
                    "không tồn tại sai lệch state để xác nhận."
                )
                rejected.append(issue)
                continue

            issue["evidence_verified"] = True
            issue["authoritative_value"] = authoritative
            verified.append(issue)
            continue

        # Semantic-only findings without a mechanically verifiable entity/dimension
        # are not auto-confirmed. They remain review-only.
        if str(issue.get("disposition") or "").upper() in {"CONFIRMED_ERROR", "NEW_ISSUE"}:
            issue["disposition"] = "NEEDS_REVIEW"
            issue["verification_note"] = "Finding ngữ nghĩa chưa có entity/dimension để backend kiểm chứng."
        issue["evidence_verified"] = None
        verified.append(issue)

    result = dict(semantic)
    result["issues_raw"] = raw
    result["issues"] = verified
    result["rejected_findings"] = rejected
    if (
        bool(deterministic_report.get("final_gate"))
        and not verified
        and rejected
        and str(result.get("verdict") or "").upper() in {"FAIL", "REVIEW_REQUIRED"}
    ):
        rejected_count = len(rejected)
        result["raw_verdict"] = result.get("verdict")
        result["verdict"] = "PASS"
        result["verdict_normalized_by_evidence_verifier"] = True
        result["summary"] = (
            f"AI nêu {rejected_count} nhận xét nhưng Evidence Verifier đã bác toàn bộ vì không khớp sổ cái đạo cụ. "
            "Rule Engine PASS. Kết luận cuối: ĐẠT."
        )
    return result


def _find_ai_match(ai_issues: list[dict], error: dict) -> dict | None:
    scene = str(error.get("scene") or "PROJECT")
    code = str(error.get("code") or "")
    matches = [
        x for x in ai_issues
        if str(x.get("scene_id") or "PROJECT") == scene
        and str(x.get("deterministic_code") or "") == code
    ]
    if not matches:
        return None
    return max(matches, key=lambda x: float(x.get("confidence") or 0))


def _arbitrate(deterministic: dict, semantic: dict) -> dict:
    det_errors = list(deterministic.get("errors") or [])
    ai_issues = list(semantic.get("issues") or [])
    effective_errors = []
    conflicts = []

    for error in det_errors:
        code = str(error.get("code") or "")
        match = _find_ai_match(ai_issues, error)
        if (
            code in SEMANTIC_CONFLICT_CODES
            and match
            and str(match.get("disposition") or "").upper() == "FALSE_POSITIVE"
            and float(match.get("confidence") or 0) >= AI_MIN_FALSE_POSITIVE
        ):
            conflicts.append({
                "scene": error.get("scene"),
                "deterministic_code": code,
                "deterministic_detail": error.get("detail"),
                "resolution": "FALSE_POSITIVE",
                "confidence": match.get("confidence"),
                "evidence": match.get("evidence"),
                "reason": match.get("reason"),
            })
            continue
        effective_errors.append(error)

    semantic_errors = []
    review_items = []
    repairable_items = []
    source_sensitive = []

    for issue in ai_issues:
        disposition = str(issue.get("disposition") or "").upper()
        confidence = float(issue.get("confidence") or 0)
        repairability = str(issue.get("repairability") or "NONE").upper()

        if disposition == "FALSE_POSITIVE":
            continue
        if disposition == "NEEDS_REVIEW" or confidence < AI_MIN_CONFIRM:
            review_items.append(issue)
            continue
        if disposition not in {"CONFIRMED_ERROR", "NEW_ISSUE"}:
            continue

        normalized = {
            "scene": issue.get("scene_id") or "PROJECT",
            "code": f"AI_{str(issue.get('issue_type') or 'CONSISTENCY').upper()}",
            "detail": issue.get("reason") or issue.get("evidence") or "AI semantic issue",
            "evidence": issue.get("evidence"),
            "confidence": confidence,
            "repairability": repairability,
            "suggested_patch_type": issue.get("suggested_patch_type") or "NONE",
        }
        semantic_errors.append(normalized)
        if repairability in {"SAFE_DERIVED", "SEMANTIC_NORMALIZATION"}:
            repairable_items.append(normalized)
        elif repairability == "SOURCE_SENSITIVE":
            source_sensitive.append(normalized)
        else:
            review_items.append(normalized)

    hard_effective = [x for x in effective_errors if str(x.get("code") or "") in HARD_RULE_CODES]
    derived_effective = [x for x in effective_errors if x not in hard_effective]

    status = "PASS"
    if hard_effective:
        status = "FAIL"
    elif source_sensitive or review_items:
        status = "REVIEW_REQUIRED"
    elif derived_effective or repairable_items:
        status = "REPAIRABLE"
    elif semantic_errors:
        status = "FAIL"
    elif not semantic.get("available"):
        status = "REVIEW_REQUIRED"
    elif str(semantic.get("verdict") or "").upper() == "REVIEW_REQUIRED":
        status = "REVIEW_REQUIRED"
    elif str(semantic.get("verdict") or "").upper() == "FAIL":
        status = "REVIEW_REQUIRED"

    return {
        "status": status,
        "final_gate": status == "PASS",
        "effective_deterministic_errors": effective_errors,
        "semantic_errors": semantic_errors,
        "validator_conflicts": conflicts,
        "repairable_items": repairable_items,
        "review_items": review_items,
        "source_sensitive_items": source_sensitive,
    }


def _scene_results(project: dict, arbitration: dict) -> list[dict]:
    by_scene = {}
    for item in (
        arbitration.get("effective_deterministic_errors", [])
        + arbitration.get("semantic_errors", [])
        + arbitration.get("review_items", [])
    ):
        by_scene.setdefault(str(item.get("scene") or item.get("scene_id") or "PROJECT"), []).append(item)
    out = []
    for scene in project.get("scenes") or []:
        sid = str(scene.get("id") or "")
        issues = by_scene.get(sid, [])
        out.append({
            "scene_id": sid,
            "passed": not issues,
            "issues": issues,
            "repairable": bool(issues) and all(
                str(x.get("repairability") or "") in {"SAFE_DERIVED", "SEMANTIC_NORMALIZATION"}
                or str(x.get("code") or "") not in HARD_RULE_CODES
                for x in issues
            ),
        })
    return out


def _apply_ledger_snapshot(structured: dict, scene: dict, snapshot: dict[str, dict]) -> dict:
    out = deepcopy(structured or {})
    props = dict(out.get("props") or {})
    entities = set(out.get("entities") or [])

    for pid in scene.get("props_present") or []:
        row = snapshot.get(str(pid))
        if not row:
            continue
        current = dict(props.get(str(pid)) or {})
        current["owner"] = row.get("owner")
        current["container"] = row.get("container")
        current["state"] = row.get("state")
        current["state_dimensions"] = deepcopy(row.get("state_dimensions") or {})
        if row.get("relations"):
            current["relations"] = deepcopy(row.get("relations") or {})
        current["resolved_from"] = "prop_ledger_v2"
        props[str(pid)] = current
        entities.add(str(pid))

    out["props"] = props
    out["entities"] = sorted(entities)
    return out


def _build_normalized_scene_states(project: dict, deterministic_report: dict) -> list[dict]:
    ledger = list(deterministic_report.get("prop_ledger") or [])
    current: dict[str, dict] = {}

    for row in ledger:
        if str(row.get("scene_id") or "") != "INITIAL":
            continue
        pid = str(row.get("prop_id") or "")
        if pid:
            current[pid] = deepcopy(row)

    rows_by_scene: dict[str, list[dict]] = {}
    for row in ledger:
        sid = str(row.get("scene_id") or "")
        if sid and sid != "INITIAL":
            rows_by_scene.setdefault(sid, []).append(row)

    result = []
    for scene in project.get("scenes") or []:
        sid = str(scene.get("id") or "")
        start_snapshot = deepcopy(current)

        for row in rows_by_scene.get(sid, []):
            pid = str(row.get("prop_id") or "")
            if pid:
                current[pid] = deepcopy(row)

        end_snapshot = deepcopy(current)
        result.append({
            "scene_id": sid,
            "start_state_structured": _apply_ledger_snapshot(
                scene.get("start_state_structured") or {},
                scene,
                start_snapshot,
            ),
            "end_state_structured": _apply_ledger_snapshot(
                scene.get("end_state_structured") or {},
                scene,
                end_snapshot,
            ),
        })
    return result


async def run_consistency_audit_v2(project_id: str, persist: bool = True) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    if persist:
        update_film_status(
            project_id,
            stage="Kiểm tra tính nhất quán: rule engine + AI semantic reviewer",
            progress=max(int(project.get("progress") or 0), 96),
        )

    deterministic = evaluate_project_production_gate(project)
    semantic = await _semantic_review(project, deterministic)
    semantic = _verify_semantic_issues(semantic, deterministic, project)
    if semantic.get("rejected_findings"):
        rechecked = await _semantic_review(project, deterministic, semantic.get("rejected_findings") or [])
        rechecked = _verify_semantic_issues(rechecked, deterministic, project)
        rechecked["recheck_performed"] = True
        rechecked["first_pass_rejected_findings"] = semantic.get("rejected_findings") or []
        semantic = rechecked
    arbitration = _arbitrate(deterministic, semantic)
    scene_results = _scene_results(project, arbitration)

    report = {
        "version": "consistency-v2.1",
        "evaluated_at": _now(),
        "status": arbitration["status"],
        "final_gate": arbitration["final_gate"],
        "state_model": "multi_dimensional_prop_state_v2",
        "deterministic": {
            "version": deterministic.get("version"),
            "final_gate": deterministic.get("final_gate"),
            "gates": deterministic.get("gates") or {},
            "error_count": deterministic.get("error_count"),
            "warning_count": deterministic.get("warning_count"),
            "errors": deterministic.get("errors") or [],
            "warnings": deterministic.get("warnings") or [],
        },
        "semantic_review": semantic,
        "rejected_ai_findings": semantic.get("rejected_findings") or [],
        "validator_conflicts": arbitration["validator_conflicts"],
        "effective_errors": arbitration["effective_deterministic_errors"] + arbitration["semantic_errors"],
        "repairable_items": arbitration["repairable_items"],
        "review_items": arbitration["review_items"],
        "source_sensitive_items": arbitration["source_sensitive_items"],
        "scene_results": scene_results,
        "summary": {
            "total_scenes": len(project.get("scenes") or []),
            "passed_scenes": sum(1 for x in scene_results if x["passed"]),
            "deterministic_gate": bool(deterministic.get("final_gate")),
            "ai_available": bool(semantic.get("available")),
            "ai_verdict": semantic.get("verdict"),
            "conflict_count": len(arbitration["validator_conflicts"]),
        },
    }

    if persist:
        save_consistency_report(project_id, report)
        prod = run_production_gate(project_id, persist=True)
        final_ready = bool(report.get("final_gate")) and bool(prod.get("final_gate"))
        semantic = report.get("semantic_review") or {}
        if final_ready:
            stage = "Kiểm tra tính nhất quán V2: PASS · đủ điều kiện tạo video"
        elif report.get("status") == "REPAIRABLE":
            stage = "Kiểm tra tính nhất quán V2: có lỗi có thể tự sửa"
        elif (
            report.get("deterministic", {}).get("final_gate")
            and not semantic.get("available")
            and semantic.get("status_code") == 429
        ):
            stage = "Rule Engine PASS · AI tạm thời bị giới hạn tốc độ · chưa mở render"
        else:
            stage = "Kiểm tra tính nhất quán V2: cần kiểm tra/sửa trước khi tạo video"
        update_film_status(
            project_id,
            status="ready" if final_ready else "needs_repair",
            stage=stage,
            progress=100,
            error=None,
        )
    return report


async def repair_consistency_v2(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    before_hashes = {str(x.get("id")): x.get("source_hash") for x in (project.get("scenes") or [])}
    before_report = await run_consistency_audit_v2(project_id, persist=True)

    if before_report.get("source_sensitive_items"):
        entry = {
            "version": "consistency-repair-v1",
            "repaired_at": _now(),
            "status": "BLOCKED_SOURCE_SENSITIVE",
            "source_mutations": 0,
            "before_status": before_report.get("status"),
            "issues": before_report.get("source_sensitive_items"),
        }
        append_repair_log(project_id, entry)
        return {"project": get_film_project(project_id), "consistency_report": before_report, "repair_log": entry}

    hard_codes = {
        str(x.get("code") or "") for x in (before_report.get("effective_errors") or [])
        if str(x.get("code") or "") in HARD_RULE_CODES
    }
    if hard_codes:
        entry = {
            "version": "consistency-repair-v1",
            "repaired_at": _now(),
            "status": "BLOCKED_HARD_RULE",
            "source_mutations": 0,
            "before_status": before_report.get("status"),
            "hard_codes": sorted(hard_codes),
        }
        append_repair_log(project_id, entry)
        return {"project": get_film_project(project_id), "consistency_report": before_report, "repair_log": entry}

    requested_patches = sorted({
        str(x.get("suggested_patch_type") or "NONE")
        for x in (before_report.get("repairable_items") or [])
        if str(x.get("suggested_patch_type") or "NONE") in SAFE_PATCH_TYPES
    })

    if before_report.get("final_gate") and not requested_patches:
        entry = {
            "version": "consistency-repair-v1",
            "repaired_at": _now(),
            "status": "NO_CHANGES",
            "before_status": before_report.get("status"),
            "after_status": before_report.get("status"),
            "applied_patch_types": [],
            "source_mutations": 0,
            "before_error_count": len(before_report.get("effective_errors") or []),
            "after_error_count": len(before_report.get("effective_errors") or []),
        }
        append_repair_log(project_id, entry)
        return {
            "project": get_film_project(project_id),
            "consistency_report": before_report,
            "repair_log": entry,
        }

    auto_repair_project_derived(project_id)

    normalize_patch_types = {
        "NORMALIZE_PROP_STATE",
        "REBUILD_STRUCTURED_STATE",
        "RELINK_CONTINUITY",
    }
    normalization_count = 0
    if any(patch in normalize_patch_types for patch in requested_patches):
        normalized_project = get_film_project(project_id)
        deterministic_after_derived = evaluate_project_production_gate(normalized_project)
        normalized_states = _build_normalized_scene_states(
            normalized_project,
            deterministic_after_derived,
        )
        if normalized_states:
            save_consistency_state_normalization(project_id, normalized_states)
            normalization_count = len(normalized_states)

    after_project = get_film_project(project_id)
    after_hashes = {str(x.get("id")): x.get("source_hash") for x in (after_project.get("scenes") or [])}
    if before_hashes != after_hashes:
        raise ValueError("SOURCE_MUTATION_BLOCKED: Consistency Repair đã làm thay đổi source hash.")

    after_report = await run_consistency_audit_v2(project_id, persist=True)
    entry = {
        "version": "consistency-repair-v1",
        "repaired_at": _now(),
        "status": "PASS" if after_report.get("final_gate") else after_report.get("status"),
        "before_status": before_report.get("status"),
        "after_status": after_report.get("status"),
        "applied_patch_types": requested_patches or ["REBUILD_DERIVED"],
        "normalized_scene_state_count": normalization_count,
        "source_mutations": 0,
        "before_error_count": len(before_report.get("effective_errors") or []),
        "after_error_count": len(after_report.get("effective_errors") or []),
        "validator_conflicts_resolved": len(after_report.get("validator_conflicts") or []),
    }
    append_repair_log(project_id, entry)
    return {
        "project": get_film_project(project_id),
        "consistency_report": after_report,
        "repair_log": entry,
    }
