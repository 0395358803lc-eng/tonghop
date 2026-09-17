import json
import uuid
from .db import connect

PROJECT_JSON_FIELDS = {
    "settings_json": ("settings", {}),
    "story_bible_json": ("story_bible", {}),
    "character_bible_json": ("characters", []),
    "location_bible_json": ("locations", []),
    "prop_bible_json": ("props", []),
    "timeline_json": ("timeline", []),
}
SCENE_JSON_FIELDS = {
    "characters_json": ("characters", []),
    "dialogue_json": ("dialogue", []),
    "continuity_json": ("continuity", {}),
    "warnings_json": ("warnings", []),
}


def _loads(value, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _scene(row):
    if not row:
        return None
    data = dict(row)
    for source, (target, default) in SCENE_JSON_FIELDS.items():
        data[target] = _loads(data.pop(source, None), default)
    return data


def _project(row, include_scenes=False):
    if not row:
        return None
    data = dict(row)
    for source, (target, default) in PROJECT_JSON_FIELDS.items():
        data[target] = _loads(data.pop(source, None), default)
    if include_scenes:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM film_scenes WHERE project_id=? ORDER BY scene_index", (data["id"],)).fetchall()
        data["scenes"] = [_scene(x) for x in rows]
    return data


def list_film_projects():
    with connect() as conn:
        rows = conn.execute("""SELECT id,name,provider,model,status,stage,progress,error,created_at,updated_at,
        (SELECT COUNT(*) FROM film_scenes s WHERE s.project_id=film_projects.id) scene_count
        FROM film_projects ORDER BY updated_at DESC""").fetchall()
    return [dict(x) for x in rows]


def create_film_project(name: str | None, original_text: str, provider: str, model: str, settings: dict):
    project_id = str(uuid.uuid4())
    title = (name or "Dự án phim mới").strip() or "Dự án phim mới"
    with connect() as conn:
        conn.execute("""INSERT INTO film_projects(id,name,original_text,provider,model,settings_json)
        VALUES(?,?,?,?,?,?)""", (project_id, title, original_text.strip(), provider, model, json.dumps(settings, ensure_ascii=False)))
    return get_film_project(project_id)


def get_film_project(project_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_projects WHERE id=?", (project_id,)).fetchone()
    return _project(row, include_scenes=True)


def delete_film_project(project_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM film_projects WHERE id=?", (project_id,))


def update_film_status(project_id: str, **values):
    allowed = {"name", "status", "stage", "progress", "master_prompt", "visual_style", "error", "settings_json"}
    patch = {k: v for k, v in values.items() if k in allowed}
    if not patch:
        return get_film_project(project_id)
    if isinstance(patch.get("settings_json"), dict):
        patch["settings_json"] = json.dumps(patch["settings_json"], ensure_ascii=False)
    parts = [f"{key}=?" for key in patch]
    args = list(patch.values()) + [project_id]
    with connect() as conn:
        conn.execute(f"UPDATE film_projects SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", args)
    return get_film_project(project_id)


def save_film_analysis(project_id: str, result: dict):
    with connect() as conn:
        conn.execute("DELETE FROM film_scenes WHERE project_id=?", (project_id,))
        conn.execute("""UPDATE film_projects SET name=?, master_prompt=?, story_bible_json=?, character_bible_json=?,
        location_bible_json=?, prop_bible_json=?, visual_style=?, timeline_json=?, status='ready',
        stage='Đã tạo Story Bible và Storyboard', progress=100, error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (
            result.get("project_title") or "Dự án phim",
            result.get("master_prompt") or "",
            json.dumps(result.get("story_bible") or {}, ensure_ascii=False),
            json.dumps(result.get("characters") or [], ensure_ascii=False),
            json.dumps(result.get("locations") or [], ensure_ascii=False),
            json.dumps(result.get("props") or [], ensure_ascii=False),
            result.get("visual_style") or "",
            json.dumps(result.get("timeline") or [], ensure_ascii=False),
            project_id,
        ))
        for index, scene in enumerate(result.get("scenes") or [], 1):
            scene_id = scene.get("id") or f"SCENE_{index:03d}"
            conn.execute("""INSERT INTO film_scenes(
            id,project_id,scene_index,title,source_text,summary,duration,characters_json,location_id,action,camera,
            lighting,atmosphere,voiceover,dialogue_json,start_state,end_state,continuity_json,visual_prompt,flow_prompt,warnings_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                scene_id, project_id, index, scene.get("title") or scene_id, scene.get("source_text") or "",
                scene.get("summary") or "", float(scene.get("duration") or 8),
                json.dumps(scene.get("characters") or [], ensure_ascii=False), scene.get("location_id"),
                scene.get("action") or "", scene.get("camera") or "", scene.get("lighting") or "",
                scene.get("atmosphere") or "", scene.get("voiceover") or "",
                json.dumps(scene.get("dialogue") or [], ensure_ascii=False), scene.get("start_state") or "",
                scene.get("end_state") or "", json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
                scene.get("visual_prompt") or "", scene.get("flow_prompt") or "",
                json.dumps(scene.get("warnings") or [], ensure_ascii=False),
            ))
    return get_film_project(project_id)


def update_film_project(project_id: str, name=None, settings=None):
    patch = {}
    if name is not None:
        patch["name"] = name.strip() or "Dự án phim"
    if settings is not None:
        patch["settings_json"] = settings
    return update_film_status(project_id, **patch)


def update_film_scene(project_id: str, scene_id: str, patch: dict):
    mapping = {"characters": "characters_json", "dialogue": "dialogue_json"}
    allowed = {"title","source_text","summary","duration","characters","location_id","action","camera","lighting",
               "atmosphere","voiceover","dialogue","start_state","end_state","visual_prompt","flow_prompt"}
    clean = {k: v for k, v in patch.items() if k in allowed and v is not None}
    impact_keys = {"characters","location_id","action","camera","duration","start_state","end_state"}
    impacted = bool(set(clean) & impact_keys)
    sql_patch = {}
    for key, value in clean.items():
        db_key = mapping.get(key, key)
        sql_patch[db_key] = json.dumps(value, ensure_ascii=False) if key in mapping else value
    if sql_patch:
        parts = [f"{key}=?" for key in sql_patch]
        args = list(sql_patch.values()) + [project_id, scene_id]
        with connect() as conn:
            conn.execute(f"UPDATE film_scenes SET {', '.join(parts)}, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?", args)
            if impacted:
                row = conn.execute("SELECT scene_index FROM film_scenes WHERE project_id=? AND id=?", (project_id, scene_id)).fetchone()
                if row:
                    next_rows = conn.execute("SELECT id,warnings_json FROM film_scenes WHERE project_id=? AND scene_index>? AND scene_index<=? ORDER BY scene_index", (project_id, row["scene_index"], row["scene_index"]+2)).fetchall()
                    warning = f"Thay đổi ở {scene_id} có thể ảnh hưởng continuity của cảnh này. Hãy kiểm tra START/END STATE."
                    for nxt in next_rows:
                        warnings = _loads(nxt["warnings_json"], [])
                        if warning not in warnings:
                            warnings.append(warning)
                        conn.execute("UPDATE film_scenes SET warnings_json=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?", (json.dumps(warnings, ensure_ascii=False), project_id, nxt["id"]))
            conn.execute("UPDATE film_projects SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (project_id,))
    return get_film_project(project_id)


def save_continuity_audit(project_id: str, scenes: list[dict]):
    with connect() as conn:
        for scene in scenes:
            conn.execute("""UPDATE film_scenes SET start_state=?, end_state=?, continuity_json=?, warnings_json=?, flow_prompt=?, updated_at=CURRENT_TIMESTAMP
            WHERE project_id=? AND id=?""", (
                scene.get("start_state") or "", scene.get("end_state") or "",
                json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
                json.dumps(scene.get("warnings") or [], ensure_ascii=False),
                scene.get("flow_prompt") or "", project_id, scene["id"],
            ))
        conn.execute("UPDATE film_projects SET stage='Continuity đã được kiểm tra', updated_at=CURRENT_TIMESTAMP WHERE id=?", (project_id,))
    return get_film_project(project_id)
