import asyncio
import json
import os
import re
from .film_store import (
    append_film_scenes,
    clear_film_scenes,
    get_film_project,
    save_continuity_audit,
    save_film_analysis,
    save_film_bible,
    update_film_status,
)
from .provider_store import get_provider
from .film_resource_store import sync_project_resources
from .film_integrity import parse_structured_source, enforce_source_snapshot, finalize_ai_integrity
from .film_compiler import attach_batch_b_compilation, build_boundary_context, compile_flow_prompt, merge_ai_scene_batch
from .film_production_gate import run_production_gate
from .providers.service import run_chat

MAX_STORY_CHARS = int(os.getenv("FILM_MAX_STORY_CHARS", "5000000"))
FILM_MODEL_TIMEOUT = int(os.getenv("FILM_MODEL_TIMEOUT", "180"))
FILM_CHUNK_CHARS = int(os.getenv("FILM_CHUNK_CHARS", "12000"))
FILM_CHUNK_OVERLAP = int(os.getenv("FILM_CHUNK_OVERLAP", "400"))
FILM_SCENE_BATCH_SIZE = int(os.getenv("FILM_SCENE_BATCH_SIZE", "12"))
FILM_FALLBACKS = {
    "xkiro": [
        "z-ai/glm-5.2",
        "openai/gpt-5.6-sol",
        "anthropic/claude-opus-5",
        "qwen/qwen3.5-flash:free",
    ],
}
NEGATIVE = (
    "identity drift, face changes, hairstyle changes, clothing changes, location changes, "
    "duplicated people, extra fingers, malformed hands, distorted anatomy, disappearing objects, "
    "teleporting characters, inconsistent props, random background changes, sudden lighting changes, "
    "unrealistic motion, floating objects, text artifacts, logos unless requested, inconsistent scale, "
    "inconsistent character age"
)


async def _run_film_model(project: dict, credentials: dict, messages: list[dict], stage_name: str = "Film AI", base_progress: int = 18) -> tuple[str, str]:
    candidates = [project["model"]]
    for model in FILM_FALLBACKS.get(project["provider"], []):
        if model not in candidates:
            candidates.append(model)
    errors = []
    for index, candidate in enumerate(candidates):
        update_film_status(project["id"], stage=f"{stage_name}: đang thử {candidate}", progress=min(base_progress + index * 2, 94))
        try:
            answer = await asyncio.wait_for(
                run_chat(project["provider"], credentials["api_key"], credentials.get("base_url"), candidate, messages),
                timeout=FILM_MODEL_TIMEOUT,
            )
            if answer.strip():
                return answer, candidate
            errors.append(f"{candidate}: empty response")
        except Exception as exc:
            errors.append(f"{candidate}: {type(exc).__name__}: {str(exc)[:260]}")
    raise RuntimeError("Không model nào hoàn tất Film Analysis: " + " | ".join(errors))


def _json_from_text(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        data = json.loads(value)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(value[start:end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("Model không trả về JSON hợp lệ")


def _compact_bible(bible: dict) -> dict:
    def slim_char(c):
        return {k: c.get(k) for k in ("id", "name", "gender", "age", "appearance", "clothing", "accessories", "signature_traits") if c.get(k)}

    def slim_loc(c):
        return {k: c.get(k) for k in ("id", "name", "type", "layout", "lighting", "time_of_day", "weather", "colors") if c.get(k)}

    def slim_prop(c):
        return {
            k: c.get(k)
            for k in (
                "id", "name", "description", "canonical_description",
                "initial_owner", "initial_state", "owner_initial", "state_initial", "owner", "state"
            )
            if c.get(k)
        }

    return {
        "project_title": bible.get("project_title"),
        "story_bible": bible.get("story_bible") or {},
        "characters": [slim_char(x) for x in (bible.get("characters") or []) if isinstance(x, dict)],
        "locations": [slim_loc(x) for x in (bible.get("locations") or []) if isinstance(x, dict)],
        "props": [slim_prop(x) for x in (bible.get("props") or []) if isinstance(x, dict)],
        "visual_style": bible.get("visual_style") or "",
        "master_prompt": (bible.get("master_prompt") or "")[:1200],
    }


def _split_text_chunks(text: str, max_chars: int, overlap: int) -> list[dict]:
    raw = text.strip()
    if not raw:
        return []
    if len(raw) <= max_chars:
        return [{"index": 1, "start": 0, "end": len(raw), "text": raw}]

    markers = [
        r"(?m)^(SCENE_\d+|SCENES?\s+\d+|#{1,3}\s+.+$|ACT\s+[IVXLC\d]+|CHƯƠNG\s+\d+|CHAPTER\s+\d+)",
        r"\n\s*\n",
    ]
    cut_points = {0, len(raw)}
    for pattern in markers:
        for match in re.finditer(pattern, raw, flags=re.I):
            cut_points.add(match.start())
    points = sorted(cut_points)

    segments = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        if b > a:
            segments.append(raw[a:b])
    if not segments:
        segments = [raw]

    chunks = []
    buf = ""
    buf_start = 0
    cursor = 0
    for seg in segments:
        if buf and len(buf) + len(seg) > max_chars:
            chunks.append({"start": buf_start, "end": cursor, "text": buf})
            overlap_text = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = overlap_text + seg
            buf_start = max(0, cursor - len(overlap_text))
        else:
            if not buf:
                buf_start = cursor
            buf += seg
        cursor += len(seg)
    if buf.strip():
        chunks.append({"start": buf_start, "end": cursor, "text": buf})

    # Hard-split any oversized remaining piece
    final = []
    for piece in chunks:
        body = piece["text"]
        if len(body) <= max_chars:
            final.append(piece)
            continue
        start = 0
        while start < len(body):
            end = min(len(body), start + max_chars)
            final.append({"start": piece["start"] + start, "end": piece["start"] + end, "text": body[start:end]})
            if end >= len(body):
                break
            start = max(end - overlap, start + 1)

    for i, item in enumerate(final, 1):
        item["index"] = i
    return final


def _canon_specificity(value) -> int:
    if value in (None, "", [], {}):
        return -1
    if isinstance(value, (int, float)):
        return 100
    if not isinstance(value, str):
        return 10
    text = value.strip()
    low = text.lower()
    score = min(len(text), 90)
    if re.fullmatch(r"\d{1,3}", text):
        score += 100
    if any(token in low for token in ("unknown", "unspecified", "generic", "default", "ngầm định", "20s", "30s", "40s", "50s", "60s", "70s")):
        score -= 40
    if any(ch.isdigit() for ch in text):
        score += 12
    return score


def _merge_canon_row(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "id" or value in (None, "", [], {}):
            continue
        old = merged.get(key)
        if old in (None, "", [], {}):
            merged[key] = value
            continue
        # Never replace a stable name with a later alias/shortening.
        if key == "name":
            continue
        # Canon is immutable once a non-empty value is locked.
        # Later chunks may fill missing fields or introduce new entities, never overwrite facts.
        continue
    return merged


def _merge_entities(base: list, incoming: list, prefix: str) -> list:
    by_id = {}
    by_name = {}
    order = []
    for item in list(base or []) + list(incoming or []):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        eid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip().lower()
        if eid and eid in by_id:
            by_id[eid] = _merge_canon_row(by_id[eid], item)
            continue
        if name and name in by_name:
            existing_id = by_name[name]
            by_id[existing_id] = _merge_canon_row(by_id[existing_id], {k: v for k, v in item.items() if k != "id"})
            continue
        if not eid:
            eid = f"{prefix}_{len(by_id) + 1:03d}"
            item["id"] = eid
        by_id[eid] = item
        order.append(eid)
        if name:
            by_name[name] = eid
    return [by_id[i] for i in order if i in by_id]


def _merge_bible(base: dict, delta: dict) -> dict:
    out = dict(base or {})
    d = delta or {}
    if d.get("project_title") and not out.get("project_title"):
        out["project_title"] = d["project_title"]
    sb = dict(out.get("story_bible") or {})
    incoming_sb = d.get("story_bible") if isinstance(d.get("story_bible"), dict) else {}
    for key, value in incoming_sb.items():
        if value and (not sb.get(key) or len(str(value)) > len(str(sb.get(key) or ""))):
            sb[key] = value
    # Append synopsis fragments carefully
    if incoming_sb.get("synopsis") and incoming_sb.get("synopsis") not in str(sb.get("synopsis") or ""):
        sb["synopsis"] = ((sb.get("synopsis") or "") + " " + incoming_sb["synopsis"]).strip()
    out["story_bible"] = sb
    out["characters"] = _merge_entities(out.get("characters") or [], d.get("characters") or [], "CHAR")
    out["locations"] = _merge_entities(out.get("locations") or [], d.get("locations") or [], "LOC")
    out["props"] = _merge_entities(out.get("props") or [], d.get("props") or [], "PROP")
    timeline = list(out.get("timeline") or [])
    for item in d.get("timeline") or []:
        if isinstance(item, dict):
            timeline.append(item)
    # Re-order timeline
    for i, item in enumerate(timeline, 1):
        item["order"] = i
    out["timeline"] = timeline
    if d.get("master_prompt"):
        out["master_prompt"] = d["master_prompt"] if not out.get("master_prompt") else out["master_prompt"]
    if d.get("visual_style"):
        out["visual_style"] = out.get("visual_style") or d["visual_style"]
    return out


def _flow_prompt(scene: dict, visual_style: str, characters: list[dict], locations: list[dict], props: list[dict] | None = None) -> str:
    prompt, meta = compile_flow_prompt(scene, visual_style, characters, locations, props or [])
    scene["flow_prompt_meta"] = meta
    return prompt


def _apply_continuity_checks(result: dict, settings: dict) -> None:
    char_ids = {x.get("id") for x in result.get("characters", []) if isinstance(x, dict) and x.get("id")}
    location_ids = {x.get("id") for x in result.get("locations", []) if isinstance(x, dict) and x.get("id")}
    auto = bool(settings.get("auto_continuity", True))
    scenes = result.get("scenes") or []
    previous = None
    for scene in scenes:
        warnings = list(scene.get("warnings") or [])
        unknown_chars = [x for x in scene.get("characters", []) if x not in char_ids]
        if unknown_chars:
            warnings.append("Character ID không có trong Character Bible: " + ", ".join(unknown_chars))
        location_id = scene.get("location_id")
        if location_id and location_id not in location_ids:
            warnings.append(f"Location ID {location_id} không có trong Location Bible.")
        for item in scene.get("dialogue") or []:
            speaker = item.get("character_id") if isinstance(item, dict) else None
            if speaker and speaker not in char_ids:
                warnings.append(f"Dialogue speaker {speaker} không có trong Character Bible.")
            elif speaker and speaker not in (scene.get("characters") or []):
                warnings.append(f"Dialogue speaker {speaker} chưa được khai báo xuất hiện trong scene.")
        words = len((scene.get("voiceover") or "").split())
        duration = float(scene.get("duration") or settings.get("scene_duration") or 8)
        if words > duration * 3.2:
            warnings.append(f"Voiceover khoảng {words} từ có thể quá dài cho {duration:g}s; nên chia scene hoặc tăng duration.")
        continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
        if previous:
            expected = previous.get("id")
            if continuity.get("previous_scene") != expected:
                continuity["previous_scene"] = expected
            if not scene.get("start_state") and previous.get("end_state") and auto:
                scene["start_state"] = previous["end_state"]
                warnings.append(f"AUTO CONTINUITY: START STATE được kế thừa từ END STATE của {expected}.")
            elif not scene.get("start_state"):
                warnings.append(f"Thiếu START STATE để đối chiếu với END STATE của {expected}.")
            if not previous.get("end_state"):
                warnings.append(f"Scene trước {expected} thiếu END STATE.")
        elif not scene.get("start_state"):
            warnings.append("Scene mở đầu chưa có START STATE rõ ràng.")
        if not scene.get("end_state"):
            warnings.append("Scene chưa có END STATE để truyền continuity cho cảnh kế tiếp.")
        scene["continuity"] = continuity
        scene["warnings"] = list(dict.fromkeys(warnings))
        previous = scene


def _scene_id(index: int) -> str:
    return f"SCENE_{index:04d}" if index > 999 else f"SCENE_{index:03d}"


def _normalize(result: dict, settings: dict) -> dict:
    for key, default in (("story_bible", {}), ("characters", []), ("locations", []), ("props", []), ("timeline", [])):
        result.setdefault(key, default)
    result.setdefault("project_title", "Dự án phim")
    result.setdefault("master_prompt", "")
    result.setdefault("visual_style", settings.get("style") or "Cinematic photorealistic film")
    scenes = result.get("scenes") or []
    if not scenes:
        raise ValueError("AI chưa tạo được scene nào từ nội dung")
    target = float(settings.get("scene_duration") or 8)
    wide = len(scenes) > 999
    for index, scene in enumerate(scenes, 1):
        scene["id"] = f"SCENE_{index:04d}" if wide else f"SCENE_{index:03d}"
        scene.setdefault("title", scene["id"])
        scene.setdefault("source_text", "")
        scene.setdefault("summary", "")
        scene["duration"] = max(1, min(float(scene.get("duration") or target), 60))
        for key, default in (("characters", []), ("dialogue", []), ("warnings", [])):
            scene.setdefault(key, default)
        for key in ("action", "camera", "lighting", "atmosphere", "voiceover", "start_state", "end_state", "visual_prompt"):
            scene.setdefault(key, "")
        scene.setdefault("location_id", None)
        previous = (f"SCENE_{index-1:04d}" if wide else f"SCENE_{index-1:03d}") if index > 1 else None
        continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
        continuity.setdefault("previous_scene", previous)
        continuity.setdefault(
            "notes",
            "Opening scene." if index == 1 else f"Direct logical continuation of {previous}; preserve state unless the story explicitly changes it.",
        )
        scene["continuity"] = continuity
        if not scene.get("flow_prompt"):
            scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"], result.get("props") or [])
    result["scenes"] = scenes
    _apply_continuity_checks(result, settings)
    for scene in result["scenes"]:
        scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"], result.get("props") or [])
    return result


async def _parse_or_repair(project: dict, credentials: dict, answer: str, preferred_model: str, stage_name: str, progress: int) -> dict:
    try:
        return _json_from_text(answer)
    except Exception:
        repair_messages = [
            {"role": "system", "content": "Bạn là JSON repair engine. Chỉ trả JSON object hợp lệ, không markdown, không thêm dữ liệu mới."},
            {"role": "user", "content": "Sửa nội dung sau thành JSON hợp lệ mà không làm mất dữ liệu:\n\n" + answer[:120000]},
        ]
        repair, _ = await _run_film_model({**project, "model": preferred_model}, credentials, repair_messages, stage_name, progress)
        return _json_from_text(repair)


def _outline_prompt(chunk: dict, total: int, settings: dict) -> list[dict]:
    schema = {
        "sections": [
            {
                "section_id": "SEC_001",
                "summary": "...",
                "key_beats": ["..."],
                "characters_mentioned": ["..."],
                "locations_mentioned": ["..."],
                "props_mentioned": ["..."],
            }
        ]
    }
    system = (
        "Bạn là Script Mapper. Tóm tắt và chia beat cho MỘT đoạn kịch bản. "
        "Không viết scene render. Chỉ trả JSON hợp lệ."
    )
    user = (
        f"Đoạn {chunk['index']}/{total}. Settings: {json.dumps(settings, ensure_ascii=False)}\n\n"
        f"NỘI DUNG ĐOẠN:\n{chunk['text']}\n\n"
        f"OUTPUT SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}\n"
        "Mỗi section là một đơn vị kể chuyện liên tục (khoảng vài phút màn ảnh). Giữ thứ tự."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _bible_from_outline_prompt(project: dict, outline: list[dict], sample_text: str) -> list[dict]:
    settings = project.get("settings") or {}
    schema = {
        "project_title": "...",
        "story_bible": {"theme": "...", "genre": "...", "purpose": "...", "audience": "...", "atmosphere": "...", "synopsis": "..."},
        "characters": [{"id": "CHAR_001", "name": "...", "gender": "...", "age": "...", "appearance": "...", "clothing": "...", "accessories": "...", "signature_traits": "...", "personality": "...", "movement": "..."}],
        "locations": [{"id": "LOC_001", "name": "...", "type": "...", "architecture": "...", "space": "...", "interior": "...", "objects": "...", "colors": "...", "lighting": "...", "time_of_day": "...", "weather": "...", "layout": "..."}],
        "props": [{"id": "PROP_001", "name": "...", "description": "...", "owner_initial": "...", "state_initial": "..."}],
        "timeline": [{"order": 1, "event": "...", "location_id": "LOC_001", "characters": ["CHAR_001"], "elapsed": "..."}],
        "master_prompt": "...",
        "visual_style": "...",
    }
    system = (
        "Bạn là Story Architect. Từ outline toàn phim + mẫu nội dung, khóa Global Story Bible. "
        "Không chia scene. ID CHAR_/LOC_/PROP_ phải ổn định. Chỉ trả JSON."
    )
    user = (
        f"PROJECT SETTINGS: {json.dumps(settings, ensure_ascii=False)}\n\n"
        f"OUTLINE TOÀN PHIM ({len(outline)} sections):\n{json.dumps(outline[:200], ensure_ascii=False)}\n\n"
        f"MẪU NỘI DUNG GỐC (đầu/cuối):\n{sample_text[:20000]}\n\n"
        f"SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _bible_chunk_prompt(project: dict, chunk: dict, total: int, locked: dict | None) -> list[dict]:
    settings = project.get("settings") or {}
    schema = {
        "characters": [{"id": "CHAR_001", "name": "...", "gender": "...", "age": "...", "appearance": "...", "clothing": "...", "accessories": "...", "signature_traits": "...", "personality": "...", "movement": "..."}],
        "locations": [{"id": "LOC_001", "name": "...", "type": "...", "architecture": "...", "space": "...", "interior": "...", "objects": "...", "colors": "...", "lighting": "...", "time_of_day": "...", "weather": "...", "layout": "..."}],
        "props": [{"id": "PROP_001", "name": "...", "description": "...", "owner_initial": "...", "state_initial": "..."}],
        "timeline": [{"order": 1, "event": "...", "location_id": "LOC_001", "characters": ["CHAR_001"], "elapsed": "..."}],
        "story_bible": {"synopsis_delta": "...", "atmosphere": "..."},
    }
    locked_ids = {
        "characters": [{"id": x.get("id"), "name": x.get("name")} for x in (locked or {}).get("characters") or []],
        "locations": [{"id": x.get("id"), "name": x.get("name")} for x in (locked or {}).get("locations") or []],
        "props": [{"id": x.get("id"), "name": x.get("name")} for x in (locked or {}).get("props") or []],
    }
    system = (
        "Bạn là Continuity Bible Editor. Bổ sung entity từ MỘT đoạn kịch bản. "
        "Tái sử dụng ID đã khóa nếu cùng nhân vật/địa điểm/đạo cụ. Chỉ trả JSON."
    )
    user = (
        f"Chunk {chunk['index']}/{total}. Settings: {json.dumps(settings, ensure_ascii=False)}\n"
        f"ID ĐÃ KHÓA:\n{json.dumps(locked_ids, ensure_ascii=False)}\n\n"
        f"NỘI DUNG ĐOẠN:\n{chunk['text']}\n\nSCHEMA:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _scenes_batch_prompt(
    project: dict,
    bible: dict,
    section: dict,
    section_text: str,
    start_index: int,
    previous_scene_id: str | None,
    previous_end_state: str,
    max_scenes: int,
    boundary_context: dict | None = None,
) -> list[dict]:
    settings = project.get("settings") or {}
    duration = settings.get("scene_duration", 8)
    compact = _compact_bible(bible)
    schema = {
        "scenes": [
            {
                "id": _scene_id(start_index),
                "title": "...",
                "source_text": "...",
                "summary": "...",
                "duration": duration,
                "characters": ["CHAR_001"],
                "location_id": "LOC_001",
                "props_present": ["PROP_001"],
                "prop_transfers": [
                    {
                        "prop_id": "PROP_001",
                        "source_owner": "CHAR_001",
                        "target_owner": "CHAR_002",
                        "moment": "..."
                    }
                ],
                "action": "...",
                "camera": "...",
                "lighting": "...",
                "atmosphere": "...",
                "voiceover": "...",
                "dialogue": [{"character_id": "CHAR_001", "text": "...", "emotion": "..."}],
                "start_state": "...",
                "end_state": "...",
                "continuity": {"previous_scene": previous_scene_id, "notes": "..."},
                "visual_prompt": "...",
                "warnings": [],
            }
        ],
        "section_complete": True,
    }
    system = (
        "Bạn là Storyboard Compiler + Continuity Supervisor, KHÔNG phải biên kịch. "
        "Chỉ chia SCENE cho ĐOẠN/SECTION hiện tại và tuyệt đối không viết lại sự kiện nguồn. "
        "source_text phải là TRÍCH ĐOẠN NGUYÊN VĂN LIÊN TỤC từ SECTION SOURCE TEXT. "
        "action chỉ được mô tả đúng hành động đã có trong source_text, không thêm/xóa/đổi thứ tự sự kiện. "
        "Mọi dialogue/voiceover phải copy nguyên văn; không được paraphrase hoặc bỏ câu thoại. "
        "Khai báo props_present và prop_transfers có cấu trúc; source_owner phải khớp current_prop_owners trong BOUNDARY CONTEXT. "
        "Không được reveal/transfer lại một prop đã chuyển owner ở chunk trước. "
        "Chỉ dùng Character/Location/Prop ID có trong Bible đã khóa. "
        "START_STATE scene đầu phải khớp previous_end_state. Chỉ trả JSON."
    )
    user = (
        f"TARGET ~{duration}s/scene. Tối đa khoảng {max_scenes} scenes cho section này (có thể ít hơn).\n"
        f"ASPECT={settings.get('aspect_ratio', '16:9')}; STYLE={settings.get('style', 'Cinematic')}; "
        f"LOCK_CHAR={settings.get('character_lock', True)}; LOCK_LOC={settings.get('location_lock', True)}.\n"
        f"START_SCENE_INDEX={start_index}; PREVIOUS_SCENE={previous_scene_id}; PREVIOUS_END_STATE={previous_end_state or 'N/A'}.\n\n"
        f"BOUNDARY CONTEXT — IMMUTABLE HISTORY:\n{json.dumps(boundary_context or {}, ensure_ascii=False)}\n\n"
        f"SECTION META:\n{json.dumps({k: section.get(k) for k in ('section_id', 'summary', 'key_beats')}, ensure_ascii=False)}\n\n"
        f"LOCKED BIBLE (compact):\n{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"SECTION SOURCE TEXT:\n{section_text}\n\n"
        f"OUTPUT SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        "Quy tắc: chia theo action/beat/dialogue/location/time/camera; không chia theo số từ; "
        "source_text PHẢI copy verbatim từ SECTION SOURCE TEXT; dialogue và voiceover giữ nguyên từng chữ; "
        "END_STATE N khớp START_STATE N+1; voiceover/dialogue phải vừa duration; "
        "không invent event/entity, không lặp lại beat đã xảy ra ở BOUNDARY CONTEXT; "
        "ID scene trong output chỉ là gợi ý và backend sẽ gán lại deterministic; "
        f"set section_complete=true nếu đã cover hết section. Negative: {NEGATIVE}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _sample_head_tail(text: str, budget: int = 18000) -> str:
    if len(text) <= budget:
        return text
    half = budget // 2
    return text[:half] + "\n\n---\n\n" + text[-half:]


def _settings_int(settings: dict, key: str, default: int) -> int:
    try:
        value = int(settings.get(key, default))
        return value if value > 0 else default
    except Exception:
        return default


async def process_film_project(project_id: str) -> None:
    project = get_film_project(project_id)
    if not project:
        return
    try:
        text = project.get("original_text") or ""
        if len(text) > MAX_STORY_CHARS:
            raise ValueError(
                f"Nội dung quá dài ({len(text):,} ký tự). Giới hạn hiện tại {MAX_STORY_CHARS:,} ký tự. "
                "Tăng FILM_MAX_STORY_CHARS nếu cần."
            )
        settings = project.get("settings") or {}

        # Batch A strict path: structured source is compiled deterministically.
        # AI is not allowed to rewrite scene action/dialogue/voiceover/prop transitions.
        strict_result = parse_structured_source(text, settings)
        if strict_result is not None:
            update_film_status(
                project_id,
                status="analyzing",
                stage="Đang khóa SOURCE + Canon + Prop Timeline",
                progress=20,
                error=None,
            )
            clear_film_scenes(project_id)
            strict_result = _normalize(strict_result, settings)
            for scene in strict_result.get("scenes") or []:
                enforce_source_snapshot(scene)
            strict_result = attach_batch_b_compilation(text, strict_result, settings)
            save_film_analysis(project_id, strict_result)
            sync_project_resources(project_id, "flow")
            run_production_gate(project_id, persist=True)
            return

        credentials = get_provider(project["provider"])
        if not credentials:
            raise ValueError("API key của nhà cung cấp đã bị gỡ")
        chunk_chars = _settings_int(settings, "film_chunk_chars", FILM_CHUNK_CHARS)
        overlap = _settings_int(settings, "film_chunk_overlap", FILM_CHUNK_OVERLAP)
        batch_size = _settings_int(settings, "film_scene_batch_size", FILM_SCENE_BATCH_SIZE)

        update_film_status(project_id, status="analyzing", stage="Đang chia kịch bản thành chunk", progress=4, error=None)
        clear_film_scenes(project_id)
        chunks = _split_text_chunks(text, chunk_chars, overlap)
        if not chunks:
            raise ValueError("Không có nội dung để phân tích")

        # 1) Outline map
        outline: list[dict] = []
        preferred_model = project["model"]
        for chunk in chunks:
            progress = 5 + int(20 * (chunk["index"] - 1) / max(len(chunks), 1))
            update_film_status(
                project_id,
                stage=f"Outline {chunk['index']}/{len(chunks)}",
                progress=min(progress, 24),
            )
            answer, preferred_model = await _run_film_model(
                {**project, "model": preferred_model},
                credentials,
                _outline_prompt(chunk, len(chunks), settings),
                f"Outline {chunk['index']}/{len(chunks)}",
                progress,
            )
            data = await _parse_or_repair(project, credentials, answer, preferred_model, "Sửa JSON Outline", progress)
            sections = data.get("sections") if isinstance(data, dict) else None
            if not isinstance(sections, list) or not sections:
                sections = [{
                    "section_id": f"SEC_{chunk['index']:03d}",
                    "summary": chunk["text"][:400],
                    "key_beats": [],
                    "characters_mentioned": [],
                    "locations_mentioned": [],
                    "props_mentioned": [],
                    "source_chunk": chunk["index"],
                    "source_text": chunk["text"],
                }]
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                sec.setdefault("section_id", f"SEC_{len(outline) + 1:03d}")
                sec["source_chunk"] = chunk["index"]
                # Source for storyboard must remain verbatim. Model summaries never become source.
                candidate_source = str(sec.get("source_text") or "")
                if not candidate_source or candidate_source not in chunk["text"]:
                    sec["source_text"] = chunk["text"]
                outline.append(sec)

        # 2) Global bible from outline + sample, then enrich per chunk
        update_film_status(project_id, stage="Đang khóa Story Bible toàn phim", progress=28)
        bible_answer, preferred_model = await _run_film_model(
            {**project, "model": preferred_model},
            credentials,
            _bible_from_outline_prompt(project, outline, _sample_head_tail(text)),
            "Story Bible",
            28,
        )
        bible = await _parse_or_repair(project, credentials, bible_answer, preferred_model, "Sửa JSON Story Bible", 32)
        for key, default in (("story_bible", {}), ("characters", []), ("locations", []), ("props", []), ("timeline", [])):
            bible.setdefault(key, default)
        bible.setdefault("project_title", project.get("name") or "Dự án phim")
        bible.setdefault("master_prompt", "")
        bible.setdefault("visual_style", settings.get("style") or "Cinematic")
        if isinstance(bible.get("story_bible"), dict):
            bible["story_bible"]["analysis_model"] = preferred_model
            bible["story_bible"]["pipeline"] = "chunked_outline_bible_batches"
            bible["story_bible"]["chunk_count"] = len(chunks)
            bible["story_bible"]["section_count"] = len(outline)

        # Enrich bible from each chunk for long scripts (entity discovery)
        if len(chunks) > 1:
            for chunk in chunks:
                progress = 33 + int(10 * (chunk["index"] - 1) / max(len(chunks), 1))
                update_film_status(project_id, stage=f"Bổ sung Bible từ chunk {chunk['index']}/{len(chunks)}", progress=min(progress, 42))
                enrich_answer, preferred_model = await _run_film_model(
                    {**project, "model": preferred_model},
                    credentials,
                    _bible_chunk_prompt(project, chunk, len(chunks), bible),
                    f"Bible chunk {chunk['index']}/{len(chunks)}",
                    progress,
                )
                try:
                    delta = await _parse_or_repair(project, credentials, enrich_answer, preferred_model, "Sửa JSON Bible chunk", progress)
                    bible = _merge_bible(bible, delta)
                except Exception:
                    continue

        save_film_bible(project_id, bible)
        sync_project_resources(project_id, "flow")
        update_film_status(project_id, stage="Story Bible đã khóa · bắt đầu Storyboard theo lô", progress=45)

        # 3) Batched storyboard per outline section
        all_scenes: list[dict] = []
        previous_scene_id = None
        previous_end_state = ""
        section_total = max(len(outline), 1)

        for sec_index, section in enumerate(outline, 1):
            section_text = section.get("source_text") or ""
            if not section_text.strip():
                continue
            # If a section text is huge, sub-split for scene batches by character windows
            sub_slices = _split_text_chunks(section_text, max(chunk_chars, 8000), overlap)
            for sub in sub_slices:
                start_index = len(all_scenes) + 1
                progress = 45 + int(40 * (sec_index - 1) / section_total)
                update_film_status(
                    project_id,
                    stage=f"Storyboard lô {sec_index}/{section_total} · {_scene_id(start_index)}+",
                    progress=min(progress, 86),
                )
                scene_project = {**project, "model": preferred_model}
                scene_answer, preferred_model = await _run_film_model(
                    scene_project,
                    credentials,
                    _scenes_batch_prompt(
                        project,
                        bible,
                        section,
                        sub["text"],
                        start_index,
                        previous_scene_id,
                        previous_end_state,
                        batch_size,
                        build_boundary_context(bible, all_scenes),
                    ),
                    f"Storyboard {sec_index}/{section_total}",
                    progress,
                )
                scene_data = await _parse_or_repair(scene_project, credentials, scene_answer, preferred_model, "Sửa JSON Storyboard", progress)
                raw_batch = scene_data.get("scenes") if isinstance(scene_data, dict) else None
                if not isinstance(raw_batch, list) or not raw_batch:
                    raise ValueError(f"Storyboard lô {sec_index} không trả về scenes hợp lệ")
                batch = merge_ai_scene_batch(
                    raw_batch,
                    sub["text"],
                    start_index,
                    bible,
                    build_boundary_context(bible, all_scenes),
                )

                append_film_scenes(project_id, batch, start_index=start_index)
                all_scenes.extend(batch)
                last = batch[-1]
                previous_scene_id = last.get("id") or _scene_id(len(all_scenes))
                previous_end_state = last.get("end_state") or previous_end_state

        if not all_scenes:
            raise ValueError("AI chưa tạo được danh sách scene hợp lệ")

        if isinstance(bible.get("story_bible"), dict):
            bible["story_bible"]["storyboard_model"] = preferred_model
            bible["story_bible"]["scene_count"] = len(all_scenes)

        update_film_status(project_id, stage="Đang khóa continuity và biên dịch Flow Prompt", progress=90)
        result = {**bible, "scenes": all_scenes}
        result = _normalize(result, settings)
        result = finalize_ai_integrity(text, result)
        for scene in result.get("scenes") or []:
            enforce_source_snapshot(scene)
        result = attach_batch_b_compilation(text, result, settings)
        save_film_analysis(project_id, result)
        sync_project_resources(project_id, "flow")
        run_production_gate(project_id, persist=True)
    except Exception as exc:
        update_film_status(project_id, status="failed", stage="Phân tích thất bại", progress=100, error=str(exc)[:3000])


def audit_film_continuity(project_id: str):
    project = get_film_project(project_id)
    if not project:
        return None
    result = {
        "characters": project.get("characters") or [],
        "locations": project.get("locations") or [],
        "props": project.get("props") or [],
        "visual_style": project.get("visual_style") or "Cinematic",
        "scenes": [dict(scene) for scene in (project.get("scenes") or [])],
    }
    _apply_continuity_checks(result, project.get("settings") or {})
    for scene in result["scenes"]:
        scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"], result.get("props") or [])
    return save_continuity_audit(project_id, result["scenes"])
