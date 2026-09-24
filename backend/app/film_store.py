import json
import uuid
from .db import connect
from .film_event_store import purge_project_events

PROJECT_JSON_FIELDS = {
    "settings_json": ("settings", {}),
    "story_bible_json": ("story_bible", {}),
    "character_bible_json": ("characters", []),
    "location_bible_json": ("locations", []),
    "prop_bible_json": ("props", []),
    "timeline_json": ("timeline", []),
    "source_manifest_json": ("source_manifest", {}),
    "integrity_json": ("integrity", {}),
    "production_gate_json": ("production_gate", {}),
    "consistency_report_json": ("consistency_report", {}),
    "repair_log_json": ("repair_log", []),
}
SCENE_JSON_FIELDS = {
    "characters_json": ("characters", []),
    "dialogue_json": ("dialogue", []),
    "continuity_json": ("continuity", {}),
    "warnings_json": ("warnings", []),
    "source_snapshot_json": ("source_snapshot", {}),
    "props_present_json": ("props_present", []),
    "prop_transfers_json": ("prop_transfers", []),
    "start_state_json": ("start_state_structured", {}),
    "end_state_json": ("end_state_structured", {}),
    "gate_json": ("gate", {}),
    "source_span_json": ("source_span", {}),
    "duration_budget_json": ("duration_budget", {}),
    "shots_json": ("shots", []),
    "flow_prompt_meta_json": ("flow_prompt_meta", {}),
    "merge_audit_json": ("merge_audit", {}),
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
        # film_pipeline_events has no foreign key, so its rows only leave in this transaction.
        events_purged = purge_project_events(conn, project_id)
        conn.execute("DELETE FROM film_projects WHERE id=?", (project_id,))
    return {"events_purged": events_purged}


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


def save_film_bible(project_id: str, bible: dict):
    """Persist locked Story Bible without wiping scenes (for long multi-batch analysis)."""
    with connect() as conn:
        conn.execute("""UPDATE film_projects SET name=?, master_prompt=?, story_bible_json=?, character_bible_json=?,
        location_bible_json=?, prop_bible_json=?, visual_style=?, timeline_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (
            bible.get("project_title") or "Dự án phim",
            bible.get("master_prompt") or "",
            json.dumps(bible.get("story_bible") or {}, ensure_ascii=False),
            json.dumps(bible.get("characters") or [], ensure_ascii=False),
            json.dumps(bible.get("locations") or [], ensure_ascii=False),
            json.dumps(bible.get("props") or [], ensure_ascii=False),
            bible.get("visual_style") or "",
            json.dumps(bible.get("timeline") or [], ensure_ascii=False),
            project_id,
        ))
    return get_film_project(project_id)


def clear_film_scenes(project_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM film_scenes WHERE project_id=?", (project_id,))


def _insert_scene_row(conn, project_id: str, index: int, scene: dict):
    scene_id = scene.get("id") or f"SCENE_{index:04d}"
    conn.execute("""INSERT INTO film_scenes(
    id,project_id,scene_index,title,source_text,summary,duration,characters_json,location_id,action,camera,
    lighting,atmosphere,voiceover,dialogue_json,start_state,end_state,continuity_json,visual_prompt,flow_prompt,warnings_json,
    source_hash,source_snapshot_json,props_present_json,prop_transfers_json,start_state_json,end_state_json,gate_json,
    source_span_json,duration_budget_json,shots_json,flow_prompt_meta_json,merge_audit_json)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        scene_id, project_id, index, scene.get("title") or scene_id, scene.get("source_text") or "",
        scene.get("summary") or "", float(scene.get("duration") or 8),
        json.dumps(scene.get("characters") or [], ensure_ascii=False), scene.get("location_id"),
        scene.get("action") or "", scene.get("camera") or "", scene.get("lighting") or "",
        scene.get("atmosphere") or "", scene.get("voiceover") or "",
        json.dumps(scene.get("dialogue") or [], ensure_ascii=False), scene.get("start_state") or "",
        scene.get("end_state") or "", json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
        scene.get("visual_prompt") or "", scene.get("flow_prompt") or "",
        json.dumps(scene.get("warnings") or [], ensure_ascii=False),
        scene.get("source_hash"),
        json.dumps(scene.get("source_snapshot") or {}, ensure_ascii=False),
        json.dumps(scene.get("props_present") or [], ensure_ascii=False),
        json.dumps(scene.get("prop_transfers") or [], ensure_ascii=False),
        json.dumps(scene.get("start_state_structured") or {}, ensure_ascii=False),
        json.dumps(scene.get("end_state_structured") or {}, ensure_ascii=False),
        json.dumps(scene.get("gate") or {}, ensure_ascii=False),
        json.dumps(scene.get("source_span") or {}, ensure_ascii=False),
        json.dumps(scene.get("duration_budget") or {}, ensure_ascii=False),
        json.dumps(scene.get("shots") or [], ensure_ascii=False),
        json.dumps(scene.get("flow_prompt_meta") or {}, ensure_ascii=False),
        json.dumps(scene.get("merge_audit") or {}, ensure_ascii=False),
    ))


def append_film_scenes(project_id: str, scenes: list[dict], start_index: int = 1):
    """Append a batch of scenes during long-running analysis."""
    with connect() as conn:
        for offset, scene in enumerate(scenes):
            _insert_scene_row(conn, project_id, start_index + offset, scene)
        conn.execute("UPDATE film_projects SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (project_id,))
    return get_film_project(project_id)


def save_film_analysis(project_id: str, result: dict):
    with connect() as conn:
        conn.execute("DELETE FROM film_scenes WHERE project_id=?", (project_id,))
        final_gate = bool((result.get("integrity") or {}).get("final_gate", False))
        status = "ready" if final_gate else "needs_repair"
        stage = "Source + Canon + Continuity Gate PASS" if final_gate else "Integrity Gate chưa đạt · cần sửa"
        conn.execute("""UPDATE film_projects SET name=?, master_prompt=?, story_bible_json=?, character_bible_json=?,
        location_bible_json=?, prop_bible_json=?, visual_style=?, timeline_json=?, source_manifest_json=?, integrity_json=?,
        production_gate_json='{}', consistency_report_json='{}', repair_log_json='[]',
        status=?, stage=?, progress=100, error=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?""", (
            result.get("project_title") or "Dự án phim",
            result.get("master_prompt") or "",
            json.dumps(result.get("story_bible") or {}, ensure_ascii=False),
            json.dumps(result.get("characters") or [], ensure_ascii=False),
            json.dumps(result.get("locations") or [], ensure_ascii=False),
            json.dumps(result.get("props") or [], ensure_ascii=False),
            result.get("visual_style") or "",
            json.dumps(result.get("timeline") or [], ensure_ascii=False),
            json.dumps(result.get("source_manifest") or {}, ensure_ascii=False),
            json.dumps(result.get("integrity") or {}, ensure_ascii=False),
            status,
            stage,
            project_id,
        ))
        for index, scene in enumerate(result.get("scenes") or [], 1):
            _insert_scene_row(conn, project_id, index, scene)
    return get_film_project(project_id)


def update_film_project(project_id: str, name=None, settings=None):
    current = get_film_project(project_id) if settings is not None else None
    old_settings = (current or {}).get("settings") or {}
    patch = {}
    if name is not None:
        patch["name"] = name.strip() or "Dự án phim"
    if settings is not None:
        patch["settings_json"] = settings
    project = update_film_status(project_id, **patch)

    gate_sensitive_settings = {
        "scene_duration", "style", "character_lock", "location_lock", "auto_continuity",
        "shot_max_seconds", "provider_shot_max_seconds",
    }
    gate_changed = settings is not None and any(
        old_settings.get(key) != settings.get(key) for key in gate_sensitive_settings
    )
    if gate_changed and project and project.get("scenes"):
        with connect() as conn:
            conn.execute(
                "UPDATE film_projects SET production_gate_json='{}', consistency_report_json='{}', status='needs_repair', stage='Dữ liệu thay đổi · cần kiểm tra lại tính nhất quán', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (project_id,),
            )
        return get_film_project(project_id)
    return project


def update_film_scene(project_id: str, scene_id: str, patch: dict):
    with connect() as conn:
        existing = conn.execute(
            """SELECT source_hash,source_text,duration,characters_json,location_id,action,voiceover,dialogue_json
            FROM film_scenes WHERE project_id=? AND id=?""",
            (project_id, scene_id),
        ).fetchone()
    source_locked = bool(existing and existing["source_hash"])
    immutable_when_locked = {
        "source_text", "duration", "characters", "location_id", "action", "voiceover", "dialogue"
    }
    current_immutable = {}
    if existing:
        current_immutable = {
            "source_text": existing["source_text"],
            "duration": float(existing["duration"] or 0),
            "characters": _loads(existing["characters_json"], []),
            "location_id": existing["location_id"],
            "action": existing["action"],
            "voiceover": existing["voiceover"],
            "dialogue": _loads(existing["dialogue_json"], []),
        }
    attempted = []
    if source_locked:
        for key in immutable_when_locked:
            value = patch.get(key)
            if value is None:
                continue
            current = current_immutable.get(key)
            if key == "duration":
                value = float(value)
            if value != current:
                attempted.append(key)
    if attempted:
        raise ValueError("SOURCE_LOCKED: Không được sửa trường nguồn đã khóa: " + ", ".join(sorted(attempted)))
    mapping = {"characters": "characters_json", "dialogue": "dialogue_json"}
    allowed = {"title","source_text","summary","duration","characters","location_id","action","camera","lighting",
               "atmosphere","voiceover","dialogue","start_state","end_state","visual_prompt","flow_prompt"}
    clean = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if source_locked:
        for key in immutable_when_locked:
            if key in clean and clean[key] == current_immutable.get(key):
                clean.pop(key, None)
    impact_keys = {"camera","lighting","atmosphere","start_state","end_state"}
    impacted = bool(set(clean) & impact_keys)
    gate_sensitive = {"camera","lighting","atmosphere","start_state","end_state","flow_prompt"}
    gate_impacted = bool(set(clean) & gate_sensitive)
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
            if gate_impacted:
                conn.execute(
                    "UPDATE film_projects SET production_gate_json='{}', consistency_report_json='{}', status='needs_repair', stage='Dữ liệu thay đổi · cần kiểm tra lại tính nhất quán', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (project_id,),
                )
            else:
                conn.execute("UPDATE film_projects SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (project_id,))
        stale_keys = {"dialogue", "voiceover", "flow_prompt", "visual_prompt"}
        if set(clean) & stale_keys:
            from .film_acceptance_snapshot import propagate_scene_change
            propagate_scene_change(project_id, scene_id, "SCENE_SOURCE_CHANGED")
    return get_film_project(project_id)


def save_continuity_audit(project_id: str, scenes: list[dict]):
    with connect() as conn:
        source_locked_project = False
        for scene in scenes:
            current = conn.execute(
                "SELECT source_hash FROM film_scenes WHERE project_id=? AND id=?",
                (project_id, scene["id"]),
            ).fetchone()
            if current and current["source_hash"]:
                source_locked_project = True
                # Source-locked scenes: continuity audit may update only derived fields.
                conn.execute("""UPDATE film_scenes SET continuity_json=?, warnings_json=?, flow_prompt=?, updated_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND id=?""", (
                    json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
                    json.dumps(scene.get("warnings") or [], ensure_ascii=False),
                    scene.get("flow_prompt") or "", project_id, scene["id"],
                ))
            else:
                conn.execute("""UPDATE film_scenes SET start_state=?, end_state=?, continuity_json=?, warnings_json=?, flow_prompt=?, updated_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND id=?""", (
                    scene.get("start_state") or "", scene.get("end_state") or "",
                    json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
                    json.dumps(scene.get("warnings") or [], ensure_ascii=False),
                    scene.get("flow_prompt") or "", project_id, scene["id"],
                ))
        stage = "Continuity Gate PASS · Source locked" if source_locked_project else "Continuity đã được kiểm tra"
        conn.execute(
            "UPDATE film_projects SET production_gate_json='{}', consistency_report_json='{}', status='needs_repair', stage=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (stage + " · Consistency V2 stale", project_id),
        )
    return get_film_project(project_id)


def save_production_gate_report(project_id: str, report: dict):
    final_gate = bool(report.get("final_gate"))
    with connect() as conn:
        row = conn.execute(
            "SELECT consistency_report_json FROM film_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        consistency = _loads(row["consistency_report_json"], {}) if row else {}
        consistency_ready = bool(consistency.get("final_gate"))
        ready = final_gate and consistency_ready
        status = "ready" if ready else "needs_repair"
        if ready:
            stage = "Consistency V2 PASS · Production Gate PASS · READY TO RENDER"
        elif final_gate:
            stage = "Production Gate PASS · chờ Consistency V2 Rule + AI"
        else:
            stage = "Production Gate FAIL · cần Auto Repair/kiểm tra"
        conn.execute(
            """UPDATE film_projects SET production_gate_json=?, status=?, stage=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (json.dumps(report or {}, ensure_ascii=False), status, stage, project_id),
        )
    return get_film_project(project_id)


def save_production_repair(project_id: str, repaired_project: dict, report: dict, log_entry: dict):
    with connect() as conn:
        row = conn.execute("SELECT repair_log_json FROM film_projects WHERE id=?", (project_id,)).fetchone()
        repair_log = _loads(row["repair_log_json"], []) if row else []
        repair_log.append(log_entry or {})
        repair_log = repair_log[-50:]

        scene_results = {
            str(item.get("scene_id")): item
            for item in (report.get("scene_results") or [])
            if isinstance(item, dict) and item.get("scene_id")
        }

        for scene in repaired_project.get("scenes") or []:
            gate = dict(scene.get("gate") or {})
            production_result = scene_results.get(str(scene.get("id")))
            if production_result:
                gate["production"] = {
                    "passed": bool(production_result.get("passed")),
                    "repairable": bool(production_result.get("repairable")),
                    "issues": production_result.get("issues") or [],
                }
            conn.execute(
                """UPDATE film_scenes SET
                camera=?, lighting=?, atmosphere=?, start_state=?, end_state=?,
                continuity_json=?, warnings_json=?, start_state_json=?, end_state_json=?, flow_prompt=?,
                source_span_json=?, duration_budget_json=?, shots_json=?, flow_prompt_meta_json=?, gate_json=?,
                updated_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND id=?""",
                (
                    scene.get("camera") or "",
                    scene.get("lighting") or "",
                    scene.get("atmosphere") or "",
                    scene.get("start_state") or "",
                    scene.get("end_state") or "",
                    json.dumps(scene.get("continuity") or {}, ensure_ascii=False),
                    json.dumps(scene.get("warnings") or [], ensure_ascii=False),
                    json.dumps(scene.get("start_state_structured") or {}, ensure_ascii=False),
                    json.dumps(scene.get("end_state_structured") or {}, ensure_ascii=False),
                    scene.get("flow_prompt") or "",
                    json.dumps(scene.get("source_span") or {}, ensure_ascii=False),
                    json.dumps(scene.get("duration_budget") or {}, ensure_ascii=False),
                    json.dumps(scene.get("shots") or [], ensure_ascii=False),
                    json.dumps(scene.get("flow_prompt_meta") or {}, ensure_ascii=False),
                    json.dumps(gate, ensure_ascii=False),
                    project_id,
                    scene.get("id"),
                ),
            )

        final_gate = bool(report.get("final_gate"))
        status = "needs_repair"
        stage = (
            "Auto Repair Production hoàn tất · cần chạy lại Consistency V2 Rule + AI"
            if final_gate
            else "Auto Repair xong · vẫn còn lỗi không thể tự sửa"
        )
        conn.execute(
            """UPDATE film_projects SET production_gate_json=?, consistency_report_json='{}', repair_log_json=?, status=?, stage=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                json.dumps(report or {}, ensure_ascii=False),
                json.dumps(repair_log, ensure_ascii=False),
                status,
                stage,
                project_id,
            ),
        )
    return get_film_project(project_id)


def save_consistency_report(project_id: str, report: dict):
    with connect() as conn:
        conn.execute(
            "UPDATE film_projects SET consistency_report_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(report or {}, ensure_ascii=False), project_id),
        )
    return get_film_project(project_id)


def append_repair_log(project_id: str, entry: dict):
    with connect() as conn:
        row = conn.execute("SELECT repair_log_json FROM film_projects WHERE id=?", (project_id,)).fetchone()
        logs = _loads(row["repair_log_json"], []) if row else []
        logs.append(entry or {})
        logs = logs[-100:]
        conn.execute(
            "UPDATE film_projects SET repair_log_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(logs, ensure_ascii=False), project_id),
        )
    return get_film_project(project_id)


def save_consistency_state_normalization(project_id: str, scene_states: list[dict]):
    with connect() as conn:
        for item in scene_states:
            scene_id = item.get("scene_id")
            if not scene_id:
                continue
            conn.execute(
                """UPDATE film_scenes SET start_state_json=?, end_state_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND id=?""",
                (
                    json.dumps(item.get("start_state_structured") or {}, ensure_ascii=False),
                    json.dumps(item.get("end_state_structured") or {}, ensure_ascii=False),
                    project_id,
                    scene_id,
                ),
            )
        conn.execute(
            """UPDATE film_projects SET production_gate_json='{}', consistency_report_json='{}',
            status='needs_repair', stage='Đã chuẩn hóa state phát sinh · đang kiểm tra lại',
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (project_id,),
        )
    return get_film_project(project_id)
