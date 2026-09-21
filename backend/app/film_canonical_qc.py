import base64
import json
import os
import re
from pathlib import Path

from .provider_store import get_provider
from .providers.vision import run_vision

QC_PROVIDER = os.getenv("FILM_CANONICAL_QC_PROVIDER", "xkiro").strip() or "xkiro"
QC_MODEL = os.getenv("FILM_CANONICAL_QC_MODEL", "qwen/qwen3-vl-plus:free").strip() or "qwen/qwen3-vl-plus:free"
QC_FALLBACK_MODELS = [
    value.strip()
    for value in os.getenv("FILM_CANONICAL_QC_FALLBACKS", "qwen/qwen3-omni-flash:free").split(",")
    if value.strip()
]
QC_OVERALL_MIN = float(os.getenv("FILM_CANONICAL_QC_OVERALL_MIN", "88"))
QC_THRESHOLDS = {
    "character": {
        "story_alignment": 90.0,
        "identity_attributes": 90.0,
        "wardrobe": 90.0,
        "face_visibility": 85.0,
        "reference_quality": 85.0,
        "single_subject": 90.0,
    },
    "location": {
        "story_alignment": 90.0,
        "geometry_layout": 88.0,
        "fixed_details": 88.0,
        "lighting_time": 82.0,
        "reference_quality": 85.0,
        "no_people": 90.0,
    },
    "prop": {
        "story_alignment": 90.0,
        "object_identity": 90.0,
        "material_color": 88.0,
        "state_details": 88.0,
        "reference_quality": 85.0,
        "isolated_object": 85.0,
    },
}


def _json_from_text(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
                return value if isinstance(value, dict) else {}
            except Exception:
                pass
    raise RuntimeError("CANONICAL_QC_INVALID_JSON: Vision model không trả JSON hợp lệ.")


def _visual_bible(resource_type: str, entity: dict) -> dict:
    if resource_type == "prop":
        allowed = {
            "id", "prop_id", "name", "title", "description", "canonical_description",
            "canonical_version", "visual_description", "appearance", "shape", "material",
            "materials", "color", "colors", "condition", "wear", "markings", "details",
        }
        return {key: value for key, value in entity.items() if key in allowed and value not in (None, "", [], {})}
    if resource_type == "character":
        ignored = {
            "initial_location", "initial_state", "owner_initial", "initial_owner",
            "transfer_rules", "scene_state", "current_state",
        }
        return {key: value for key, value in entity.items() if key not in ignored and value not in (None, "", [], {})}
    if resource_type == "location":
        ignored = {"characters_present", "scene_state", "current_state", "owner_initial", "initial_owner"}
        return {key: value for key, value in entity.items() if key not in ignored and value not in (None, "", [], {})}
    return dict(entity)


def _prompt(project: dict, resource_type: str, entity_id: str, entity: dict) -> str:
    thresholds = QC_THRESHOLDS[resource_type]
    schema_scores = ",\n".join(f'    "{key}": 0' for key in thresholds)
    bible = json.dumps(_visual_bible(resource_type, entity), ensure_ascii=False, indent=2)
    style = str(project.get("visual_style") or (project.get("settings") or {}).get("style") or "Cinematic")
    type_rules = {
        "character": (
            "Ảnh phải có đúng MỘT nhân vật chính, khuôn mặt đủ rõ để dùng làm identity reference; "
            "đặc điểm khuôn mặt/tóc/tuổi tương đối/giới tính biểu hiện/trang phục/phụ kiện phải khớp Story Bible. "
            "Không chấp nhận mặt bị che, nhiều người, alternate outfit, chữ/logo/watermark, collage hoặc biến dạng giải phẫu."
        ),
        "location": (
            "Ảnh phải là một establishing reference của đúng bối cảnh, không có người; kiến trúc, layout, cửa/cửa sổ, "
            "đồ vật cố định, màu, ánh sáng, thời gian trong ngày và thời tiết phải khớp Story Bible. "
            "Không chấp nhận layout mơ hồ, kiến trúc khác, collage, chữ/watermark."
        ),
        "prop": (
            "Ảnh phải thể hiện đúng MỘT đạo cụ chính; hình dạng, vật liệu, màu sắc, độ cũ và trạng thái nội tại của vật thể "
            "phải khớp VISUAL BIBLE. Đạo cụ phải rõ, gần như cô lập, dễ dùng làm visual identity reference. "
            "KHÔNG đánh giá vị trí của đạo cụ trong một scene, chủ sở hữu, góc trái/phải của bàn, chuyển giao hay quan hệ với nhân vật; "
            "các thông tin đó thuộc Scene Continuity chứ không thuộc Canonical Asset QC. "
            "Không chấp nhận vật thể sai loại, người thừa, collage, chữ/watermark."
        ),
    }[resource_type]
    return f"""Bạn là Canonical Visual Asset QC cho pipeline phim nhiều cảnh.
Nhiệm vụ: quyết định ảnh này CÓ ĐỦ CHÍNH XÁC để trở thành ảnh chuẩn khóa xuyên suốt toàn bộ phim hay không.
Chỉ đánh giá chi tiết thực sự nhìn thấy trong ảnh. Không tự bù chi tiết bị thiếu. Không ưu ái vì ảnh đẹp.
Phong cách dự án: {style}
Asset: {resource_type}:{entity_id}

QUY TẮC:
{type_rules}

STORY BIBLE:
{bible}

Chấm 0-100 từng dimension. Backend sẽ tự áp ngưỡng cứng; bạn không được bỏ thiếu dimension.
Chỉ trả JSON object, không markdown:
{{
  "overall_score": 0,
  "observed_summary": "...",
  "dimension_scores": {{
{schema_scores}
  }},
  "issues": [
    {{
      "dimension": "...",
      "severity": "low|medium|high|critical",
      "evidence": "chi tiết thực tế quan sát thấy",
      "expected": "chi tiết Story Bible yêu cầu",
      "repair_instruction": "chỉ dẫn cụ thể để tạo lại ảnh"
    }}
  ],
  "recommended_repair_prompt": "Một đoạn ngắn nêu chính xác những gì phải sửa khi regenerate; để trống nếu không có lỗi."
}}"""


def _apply_hard_gate(data: dict, resource_type: str) -> dict:
    scores = data.get("dimension_scores")
    if not isinstance(scores, dict):
        scores = {}
        data["dimension_scores"] = scores
    results = {}
    for name, threshold in QC_THRESHOLDS[resource_type].items():
        raw = scores.get(name)
        try:
            score = float(raw)
        except Exception:
            score = None
        passed = score is not None and score >= threshold
        results[name] = {"score": score, "threshold": threshold, "passed": passed}

    try:
        overall = float(data.get("overall_score"))
    except Exception:
        overall = None
    overall_pass = overall is not None and overall >= QC_OVERALL_MIN
    hard_pass = overall_pass and all(item["passed"] for item in results.values())
    data["overall_score"] = overall
    data["hard_gate"] = {
        "version": "canonical-visual-qc-v1",
        "passed": hard_pass,
        "overall": {"score": overall, "threshold": QC_OVERALL_MIN, "passed": overall_pass},
        "dimensions": results,
    }
    data["passed"] = hard_pass
    return data


def _repair_feedback(report: dict) -> str:
    gate = report.get("hard_gate") or {}
    dimensions = gate.get("dimensions") or {}
    failures = []
    for name, item in dimensions.items():
        if not item.get("passed"):
            score = item.get("score")
            threshold = item.get("threshold")
            failures.append(f"{name}: {score if score is not None else 'missing'}/100, cần >= {threshold}")
    issues = report.get("issues") or []
    instructions = [
        str(item.get("repair_instruction") or "").strip()
        for item in issues
        if isinstance(item, dict) and str(item.get("repair_instruction") or "").strip()
    ]
    recommended = str(report.get("recommended_repair_prompt") or "").strip()
    pieces = []
    if failures:
        pieces.append("Hard-gate failed: " + "; ".join(failures))
    if instructions:
        pieces.append("Fix exactly: " + " | ".join(instructions[:8]))
    if recommended:
        pieces.append(recommended)
    return " ".join(pieces)[:5000]


async def evaluate_canonical_asset(
    project: dict,
    resource_type: str,
    entity_id: str,
    entity: dict,
    image_path: str | Path,
) -> dict:
    if resource_type not in QC_THRESHOLDS:
        raise ValueError("Resource type QC không hợp lệ.")
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError("CANONICAL_QC_IMAGE_MISSING: Không tìm thấy ảnh canonical để kiểm tra.")

    credentials = get_provider(QC_PROVIDER)
    if not credentials:
        raise RuntimeError(f"CANONICAL_QC_PROVIDER_MISSING: Chưa cấu hình {QC_PROVIDER} cho Vision QC.")

    image = {
        "label": f"CANONICAL CANDIDATE {resource_type.upper()} {entity_id}",
        "timestamp": 0.0,
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
    prompt = _prompt(project, resource_type, entity_id, entity)
    candidates = [QC_MODEL] + [model for model in QC_FALLBACK_MODELS if model != QC_MODEL]
    errors = []
    for model in candidates:
        try:
            raw = await run_vision(
                QC_PROVIDER,
                credentials["api_key"],
                credentials.get("base_url"),
                model,
                prompt,
                [image],
            )
            report = _apply_hard_gate(_json_from_text(raw), resource_type)
            report.update({
                "provider": QC_PROVIDER,
                "model": model,
                "asset_type": resource_type,
                "entity_id": entity_id,
                "repair_feedback": _repair_feedback(report),
            })
            return report
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:500]}")
    raise RuntimeError("CANONICAL_QC_UNAVAILABLE: " + " | ".join(errors)[:1800])
