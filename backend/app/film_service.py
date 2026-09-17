import asyncio
import json
import os
import re
from .film_store import get_film_project, save_continuity_audit, save_film_analysis, update_film_status
from .provider_store import get_provider
from .providers.service import run_chat

MAX_STORY_CHARS = int(os.getenv("FILM_MAX_STORY_CHARS", "160000"))
FILM_MODEL_TIMEOUT = int(os.getenv("FILM_MODEL_TIMEOUT", "30"))
FILM_FALLBACKS = {"xkiro": ["qwen/qwen3.7-flash:free", "qwen/qwen3.5-flash:free", "minimax/minimax-m2.5-highspeed:free", "deepseek/deepseek-v4.1-flash:free"]}
NEGATIVE = "identity drift, face changes, hairstyle changes, clothing changes, location changes, duplicated people, extra fingers, malformed hands, distorted anatomy, disappearing objects, teleporting characters, inconsistent props, random background changes, sudden lighting changes, unrealistic motion, floating objects, text artifacts, logos unless requested, inconsistent scale, inconsistent character age"


async def _run_film_model(project: dict, credentials: dict, messages: list[dict], stage_name: str = "Film AI", base_progress: int = 18) -> tuple[str, str]:
    candidates = [project["model"]]
    for model in FILM_FALLBACKS.get(project["provider"], []):
        if model not in candidates:
            candidates.append(model)
    errors = []
    for index, candidate in enumerate(candidates):
        update_film_status(project["id"], stage=f"{stage_name}: đang thử {candidate}", progress=min(base_progress + index * 3, 86))
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


def _flow_prompt(scene: dict, visual_style: str, characters: list[dict], locations: list[dict]) -> str:
    char_map = {x.get("id"): x for x in characters if isinstance(x, dict) and x.get("id")}
    loc_map = {x.get("id"): x for x in locations if isinstance(x, dict) and x.get("id")}
    char_blocks = [json.dumps(char_map[x], ensure_ascii=False) for x in (scene.get("characters") or []) if x in char_map]
    chars = "\n".join(char_blocks) or ", ".join(scene.get("characters") or []) or "None"
    location = loc_map.get(scene.get("location_id"))
    location_text = json.dumps(location, ensure_ascii=False) if location else (scene.get("location_id") or "Unspecified")
    continuity = (scene.get("continuity") or {}).get("notes") or "Maintain continuity with the previous scene."
    return (
        f"SCENE ID: {scene['id']}\n\nContinuity:\n{continuity}\n\n"
        f"Character:\n{chars}\n\nLocation:\n{location_text}\n\n"
        f"Action:\n{scene.get('action') or scene.get('summary') or ''}\n\n"
        f"Camera:\n{scene.get('camera') or 'Cinematic coverage appropriate to the action.'}\n\n"
        f"Lighting:\n{scene.get('lighting') or 'Consistent cinematic lighting.'}\n\n"
        f"Environment:\n{scene.get('atmosphere') or 'Maintain the established environment.'}\n\n"
        f"Visual style:\n{visual_style}\n\nStart frame:\n{scene.get('start_state') or ''}\n\n"
        f"End frame:\n{scene.get('end_state') or ''}\n\n"
        "Consistency requirements:\nMaintain exact character identity, outfit, environment, props and visual style from previous scene.\n\n"
        f"Avoid:\n{NEGATIVE}."
    )



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
    for index, scene in enumerate(scenes, 1):
        scene["id"] = f"SCENE_{index:03d}"
        scene.setdefault("title", scene["id"])
        scene.setdefault("source_text", "")
        scene.setdefault("summary", "")
        scene["duration"] = max(1, min(float(scene.get("duration") or target), 60))
        for key, default in (("characters", []), ("dialogue", []), ("warnings", [])):
            scene.setdefault(key, default)
        for key in ("action", "camera", "lighting", "atmosphere", "voiceover", "start_state", "end_state", "visual_prompt"):
            scene.setdefault(key, "")
        scene.setdefault("location_id", None)
        previous = f"SCENE_{index-1:03d}" if index > 1 else None
        continuity = scene.get("continuity") if isinstance(scene.get("continuity"), dict) else {}
        continuity.setdefault("previous_scene", previous)
        continuity.setdefault("notes", "Opening scene." if index == 1 else f"Direct logical continuation of {previous}; preserve state unless the story explicitly changes it.")
        scene["continuity"] = continuity
        if not scene.get("flow_prompt"):
            scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"])
    result["scenes"] = scenes
    _apply_continuity_checks(result, settings)
    for scene in result["scenes"]:
        scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"])
    return result



def _bible_prompt(project: dict) -> list[dict]:
    settings = project.get("settings") or {}
    schema = {
        "project_title": "...",
        "story_bible": {"theme":"...","genre":"...","purpose":"...","audience":"...","atmosphere":"...","synopsis":"..."},
        "characters": [{"id":"CHAR_001","name":"...","gender":"...","age":"...","appearance":"...","clothing":"...","accessories":"...","signature_traits":"...","personality":"...","movement":"..."}],
        "locations": [{"id":"LOC_001","name":"...","type":"...","architecture":"...","space":"...","interior":"...","objects":"...","colors":"...","lighting":"...","time_of_day":"...","weather":"...","layout":"..."}],
        "props": [{"id":"PROP_001","name":"...","description":"...","owner":"...","state":"..."}],
        "timeline": [{"order":1,"event":"...","location_id":"LOC_001","characters":["CHAR_001"],"elapsed":"..."}],
        "master_prompt": "...",
        "visual_style": "...",
    }
    system = (
        "Bạn là Story Architect. Đọc TOÀN BỘ nội dung trước khi phân tích. Xây một Story World ổn định cho sản xuất phim AI. "
        "Không chia scene ở bước này. Character/Location/Prop ID phải duy nhất và có thể tái sử dụng. "
        "Chỉ trả một JSON object hợp lệ, không markdown."
    )
    user = (
        f"PROJECT SETTINGS: {json.dumps(settings, ensure_ascii=False)}\n\n"
        f"NỘI DUNG GỐC:\n{project['original_text']}\n\n"
        "Tạo Global Story Bible theo schema:\n" + json.dumps(schema, ensure_ascii=False) +
        "\nGiữ ngoại hình, trang phục, kiến trúc, đạo cụ, thời gian và logic sự kiện nhất quán. Không bịa trái nội dung gốc."
    )
    return [{"role":"system","content":system},{"role":"user","content":user}]


def _scenes_prompt(project: dict, bible: dict) -> list[dict]:
    settings = project.get("settings") or {}
    duration = settings.get("scene_duration", 8)
    context = {k: bible.get(k) for k in ("story_bible","characters","locations","props","timeline","master_prompt","visual_style")}
    schema = {"scenes": [{
        "id":"SCENE_001","title":"...","source_text":"...","summary":"...","duration":duration,
        "characters":["CHAR_001"],"location_id":"LOC_001","action":"...","camera":"...","lighting":"...",
        "atmosphere":"...","voiceover":"...","dialogue":[{"character_id":"CHAR_001","text":"...","emotion":"..."}],
        "start_state":"...","end_state":"...","continuity":{"previous_scene":None,"notes":"..."},"visual_prompt":"...","warnings":[]
    }]}
    system = (
        "Bạn là Storyboard Director và Continuity Supervisor. Dựa trên Story Bible đã khóa, chia nội dung thành các clip có thể render độc lập "
        "nhưng vẫn thuộc cùng một bộ phim. Chỉ trả JSON hợp lệ, không markdown."
    )
    user = (
        f"TARGET DURATION: {duration}s/scene. ASPECT: {settings.get('aspect_ratio','16:9')}. STYLE: {settings.get('style','Cinematic')}.\n"
        f"LOCK CHARACTER={settings.get('character_lock',True)}; LOCK LOCATION={settings.get('location_lock',True)}; AUTO CONTINUITY={settings.get('auto_continuity',True)}.\n\n"
        "LOCKED PROJECT CONTEXT:\n" + json.dumps(context, ensure_ascii=False) +
        "\n\nORIGINAL STORY:\n" + project["original_text"] +
        "\n\nOUTPUT SCHEMA:\n" + json.dumps(schema, ensure_ascii=False) +
        "\n\nQuy tắc: chia theo hành động/ý nghĩa/lời thoại/location/time/camera/rhythm; không chia theo số từ. "
        "END STATE của scene trước phải nối hợp lý với START STATE của scene sau. Nếu voiceover hoặc dialogue quá dài, tách thêm scene. "
        "Chỉ dùng Character/Location ID có trong Bible. Visual prompt phải mô tả subject, appearance/clothing, location, action, camera, lighting, atmosphere, style, continuity và end frame."
    )
    return [{"role":"system","content":system},{"role":"user","content":user}]


async def _parse_or_repair(project: dict, credentials: dict, answer: str, preferred_model: str, stage_name: str, progress: int) -> dict:
    try:
        return _json_from_text(answer)
    except Exception:
        repair_messages = [
            {"role":"system","content":"Bạn là JSON repair engine. Chỉ trả JSON object hợp lệ, không markdown, không thêm dữ liệu mới."},
            {"role":"user","content":"Sửa nội dung sau thành JSON hợp lệ mà không làm mất dữ liệu:\n\n" + answer[:120000]},
        ]
        repair, _ = await _run_film_model({**project, "model": preferred_model}, credentials, repair_messages, stage_name, progress)
        return _json_from_text(repair)

def _analysis_prompt(project: dict) -> list[dict]:
    settings = project.get("settings") or {}
    duration = settings.get("scene_duration", 8)
    system = (
        "Bạn là Story Architect và Continuity Supervisor cho hệ thống sản xuất phim AI. "
        "Bắt buộc đọc và hiểu TOÀN BỘ nội dung trước khi chia scene. Không được coi các scene là video độc lập. "
        "Hãy xây Story World, Master Context, Character/Location/Prop Bible, timeline và continuity state rồi mới chia scene. "
        "Trả về CHỈ MỘT JSON object hợp lệ, không markdown. Không bịa chi tiết trái nội dung gốc."
    )
    schema = {
        "project_title": "...",
        "story_bible": {"theme":"...","genre":"...","purpose":"...","audience":"...","atmosphere":"...","synopsis":"..."},
        "characters": [{"id":"CHAR_001","name":"...","gender":"...","age":"...","appearance":"...","clothing":"...","accessories":"...","signature_traits":"...","personality":"...","movement":"..."}],
        "locations": [{"id":"LOC_001","name":"...","type":"...","architecture":"...","space":"...","interior":"...","objects":"...","colors":"...","lighting":"...","time_of_day":"...","weather":"...","layout":"..."}],
        "props": [{"id":"PROP_001","name":"...","description":"...","owner":"...","state":"..."}],
        "timeline": [{"order":1,"event":"...","location_id":"LOC_001","characters":["CHAR_001"],"elapsed":"..."}],
        "master_prompt": "Master Project Prompt...",
        "visual_style": "GLOBAL VISUAL STYLE...",
        "scenes": [{"id":"SCENE_001","title":"...","source_text":"...","summary":"...","duration":duration,"characters":["CHAR_001"],"location_id":"LOC_001","action":"...","camera":"...","lighting":"...","atmosphere":"...","voiceover":"...","dialogue":[{"character_id":"CHAR_001","text":"...","emotion":"..."}],"start_state":"...","end_state":"...","continuity":{"previous_scene":None,"notes":"..."},"visual_prompt":"...","flow_prompt":"...","warnings":[]}]
    }
    user = (
        f"Thiết lập dự án:\n- Scene target duration: {duration}s\n"
        f"- Aspect ratio: {settings.get('aspect_ratio', '16:9')}\n"
        f"- Resolution: {settings.get('resolution', '1080p')}\n"
        f"- Style: {settings.get('style', 'Cinematic')}\n"
        f"- Lock character: {settings.get('character_lock', True)}\n"
        f"- Lock location: {settings.get('location_lock', True)}\n"
        f"- Auto continuity: {settings.get('auto_continuity', True)}\n\n"
        f"NỘI DUNG GỐC:\n{project['original_text']}\n\n"
        "Hãy trả JSON theo schema logic sau:\n" + json.dumps(schema, ensure_ascii=False, indent=2) +
        "\n\nQuy tắc bắt buộc:\n"
        "1) Character ID, Location ID, Prop ID phải ổn định xuyên suốt.\n"
        "2) Không đổi trang phục/location/prop nếu story không có sự kiện hợp lý.\n"
        "3) END STATE scene N phải tương thích START STATE scene N+1.\n"
        f"4) Nếu voiceover/dialogue quá dài cho {duration}s, chia thêm scene nhưng giữ cùng context.\n"
        "5) Scene được chia theo hành động, ý nghĩa, lời thoại, địa điểm, thời gian, góc nhìn và nhịp kể; không chia theo số từ.\n"
        "6) Flow prompt phải chứa continuity, character, location, action, camera, lighting, environment, visual style, start/end frame, consistency requirements.\n"
        f"7) Negative constraints: {NEGATIVE}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def process_film_project(project_id: str) -> None:
    project = get_film_project(project_id)
    if not project:
        return
    try:
        if len(project["original_text"]) > MAX_STORY_CHARS:
            raise ValueError(f"Nội dung quá dài cho một lượt phân tích ({len(project['original_text']):,} ký tự). Giới hạn hiện tại {MAX_STORY_CHARS:,} ký tự.")
        credentials = get_provider(project["provider"])
        if not credentials:
            raise ValueError("API key của nhà cung cấp đã bị gỡ")

        update_film_status(project_id, status="analyzing", stage="Đang đọc toàn bộ nội dung", progress=8, error=None)
        bible_answer, bible_model = await _run_film_model(
            project, credentials, _bible_prompt(project), "Story Bible", 15)
        bible = await _parse_or_repair(project, credentials, bible_answer, bible_model, "Sửa JSON Story Bible", 38)
        for key, default in (("story_bible", {}), ("characters", []), ("locations", []), ("props", []), ("timeline", [])):
            bible.setdefault(key, default)
        bible.setdefault("project_title", project.get("name") or "Dự án phim")
        bible.setdefault("master_prompt", "")
        bible.setdefault("visual_style", (project.get("settings") or {}).get("style") or "Cinematic")
        if isinstance(bible.get("story_bible"), dict):
            bible["story_bible"]["analysis_model"] = bible_model

        update_film_status(project_id, stage="Story Bible đã khóa · đang chia Storyboard", progress=48)
        scene_project = {**project, "model": bible_model}
        scene_answer, scene_model = await _run_film_model(
            scene_project, credentials, _scenes_prompt(project, bible), "Storyboard", 55)
        scene_data = await _parse_or_repair(scene_project, credentials, scene_answer, scene_model, "Sửa JSON Storyboard", 76)
        scenes = scene_data.get("scenes") if isinstance(scene_data, dict) else None
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("AI chưa tạo được danh sách scene hợp lệ")
        if isinstance(bible.get("story_bible"), dict):
            bible["story_bible"]["storyboard_model"] = scene_model
        result = {**bible, "scenes": scenes}

        update_film_status(project_id, stage="Đang khóa continuity và biên dịch Flow Prompt", progress=86)
        result = _normalize(result, project.get("settings") or {})
        save_film_analysis(project_id, result)
    except Exception as exc:
        update_film_status(project_id, status="failed", stage="Phân tích thất bại", progress=100, error=str(exc)[:3000])

def audit_film_continuity(project_id: str):
    project = get_film_project(project_id)
    if not project:
        return None
    result = {
        "characters": project.get("characters") or [],
        "locations": project.get("locations") or [],
        "visual_style": project.get("visual_style") or "Cinematic",
        "scenes": [dict(scene) for scene in (project.get("scenes") or [])],
    }
    _apply_continuity_checks(result, project.get("settings") or {})
    for scene in result["scenes"]:
        scene["flow_prompt"] = _flow_prompt(scene, result["visual_style"], result["characters"], result["locations"])
    return save_continuity_audit(project_id, result["scenes"])
