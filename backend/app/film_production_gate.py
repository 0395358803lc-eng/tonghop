from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .film_compiler import (
    build_duration_budget,
    build_shot_plan,
    compile_flow_prompt,
    compute_source_spans,
)
from .film_integrity import (
    _canon_completeness_errors,
    _dialogue_gate,
    _event_ledger,
    _prop_ledger,
    _transition_gate,
    _voiceover_gate,
    structure_state,
    verify_source_lock,
)
from .film_store import (
    get_film_project,
    save_production_gate_report,
    save_production_repair,
)


REPAIRABLE_CODES = {
    "SOURCE_SPAN_STALE",
    "SHOT_PLAN_STALE",
    "FLOW_PROMPT_STALE",
    "FLOW_PROMPT_HASH_MISMATCH",
    "SHOT_PROMPT_STALE",
    "PREVIOUS_SCENE_LINK",
    "DERIVED_CAMERA_MISSING",
    "DERIVED_LIGHTING_MISSING",
    "DERIVED_ATMOSPHERE_MISSING",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot_signature(shot: dict) -> dict:
    return {
        "id": shot.get("id"),
        "scene_id": shot.get("scene_id"),
        "shot_index": shot.get("shot_index"),
        "duration": round(float(shot.get("duration") or 0), 3),
        "action": shot.get("action") or "",
        "dialogue": shot.get("dialogue") or [],
        "voiceover": shot.get("voiceover") or "",
        "characters": shot.get("characters") or [],
        "location_id": shot.get("location_id"),
        "props_present": shot.get("props_present") or [],
        "start_state": shot.get("start_state") or "",
        "end_state": shot.get("end_state") or "",
    }


def _prompt_hash_matches(prompt: str, meta: dict) -> bool:
    import hashlib
    actual = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()
    return bool(meta.get("prompt_hash")) and meta.get("prompt_hash") == actual


def _scene_semantic_copy(scene: dict) -> dict:
    item = deepcopy(scene)
    item["start_state_structured"] = structure_state(
        item.get("start_state") or "",
        item.get("characters") or [],
        item.get("location_id"),
        item.get("props_present") or [],
    )
    item["end_state_structured"] = structure_state(
        item.get("end_state") or "",
        item.get("characters") or [],
        item.get("location_id"),
        item.get("props_present") or [],
    )
    return item


def _add(errors: list[dict], scene: str, code: str, detail: str) -> None:
    errors.append({"scene": scene, "code": code, "detail": detail})


def evaluate_project_production_gate(project: dict) -> dict:
    scenes = [_scene_semantic_copy(x) for x in (project.get("scenes") or [])]
    characters = project.get("characters") or []
    locations = project.get("locations") or []
    props = project.get("props") or []
    settings = project.get("settings") or {}
    errors: list[dict] = []
    warnings: list[dict] = []

    # 1. Source lock is checked fresh against persisted snapshots.
    for scene in scenes:
        ok, reason = verify_source_lock(scene)
        if not ok:
            _add(errors, str(scene.get("id") or "UNKNOWN"), "SOURCE_LOCK_FAILED", str(reason or "unknown"))

    # 2. Canon completeness and referential integrity.
    errors.extend(_canon_completeness_errors(characters, locations, props))
    char_ids = {str(x.get("id")) for x in characters if isinstance(x, dict) and x.get("id")}
    loc_ids = {str(x.get("id")) for x in locations if isinstance(x, dict) and x.get("id")}
    prop_ids = {str(x.get("id")) for x in props if isinstance(x, dict) and x.get("id")}

    for scene in scenes:
        sid = str(scene.get("id") or "UNKNOWN")
        for cid in scene.get("characters") or []:
            if str(cid) not in char_ids:
                _add(errors, sid, "UNKNOWN_CHARACTER", str(cid))
        lid = scene.get("location_id")
        if lid and str(lid) not in loc_ids:
            _add(errors, sid, "UNKNOWN_LOCATION", str(lid))
        for pid in scene.get("props_present") or []:
            if str(pid) not in prop_ids:
                _add(errors, sid, "UNKNOWN_PROP", str(pid))

    # 3. Semantic continuity, dialogue, voice, prop ownership/state and event order.
    errors.extend(_dialogue_gate(scenes, characters))
    errors.extend(_voiceover_gate(scenes))
    errors.extend(_transition_gate(scenes))
    prop_ledger, prop_errors, prop_warnings = _prop_ledger(props, scenes)
    event_ledger, event_errors = _event_ledger(scenes, prop_ledger)
    errors.extend(prop_errors)
    errors.extend(event_errors)
    warnings.extend(prop_warnings)

    # 4. Source order must still match the original script.
    spans, span_errors = compute_source_spans(project.get("original_text") or "", scenes)
    errors.extend(span_errors)

    # 5. Duration, shot plan, deterministic Flow prompt fidelity.
    duration_errors: list[dict] = []
    shot_errors: list[dict] = []
    prompt_errors: list[dict] = []
    derived_errors: list[dict] = []

    for scene in scenes:
        sid = str(scene.get("id") or "UNKNOWN")

        expected_span = spans.get(sid) or {}
        if expected_span and (scene.get("source_span") or {}) != expected_span:
            _add(shot_errors, sid, "SOURCE_SPAN_STALE", "Stored source span differs from current source order.")

        budget = build_duration_budget(scene, settings)
        if not budget.get("passed"):
            _add(
                duration_errors,
                sid,
                "DURATION_BUDGET_EXCEEDED",
                f"required={budget.get('required_seconds')}s declared={budget.get('declared_seconds')}s",
            )

        expected_shots, expected_budget = build_shot_plan(scene, settings)
        current_shots = scene.get("shots") or []
        if (
            (scene.get("duration_budget") or {}) != expected_budget
            or [_shot_signature(x) for x in current_shots] != [_shot_signature(x) for x in expected_shots]
        ):
            _add(shot_errors, sid, "SHOT_PLAN_STALE", "Stored shot plan no longer matches scene duration/source facts.")

        expected_prompt, expected_meta = compile_flow_prompt(
            scene,
            project.get("visual_style") or "Cinematic",
            characters,
            locations,
            props,
        )
        if scene.get("flow_prompt") != expected_prompt:
            _add(prompt_errors, sid, "FLOW_PROMPT_STALE", "Scene Flow Prompt differs from deterministic compiler output.")
        if not _prompt_hash_matches(scene.get("flow_prompt") or "", scene.get("flow_prompt_meta") or {}):
            _add(prompt_errors, sid, "FLOW_PROMPT_HASH_MISMATCH", "Stored Flow Prompt hash is invalid.")
        if (scene.get("flow_prompt_meta") or {}).get("prompt_hash") != expected_meta.get("prompt_hash"):
            _add(prompt_errors, sid, "FLOW_PROMPT_STALE", "Stored Flow Prompt metadata hash is stale.")

        if len(current_shots) == len(expected_shots):
            for current, expected in zip(current_shots, expected_shots):
                shot_prompt, shot_meta = compile_flow_prompt(
                    scene,
                    project.get("visual_style") or "Cinematic",
                    characters,
                    locations,
                    props,
                    shot=expected,
                )
                if current.get("flow_prompt") != shot_prompt:
                    _add(prompt_errors, sid, "SHOT_PROMPT_STALE", str(expected.get("id")))
                if not _prompt_hash_matches(current.get("flow_prompt") or "", current.get("flow_prompt_meta") or {}):
                    _add(prompt_errors, sid, "FLOW_PROMPT_HASH_MISMATCH", str(expected.get("id")))
                if (current.get("flow_prompt_meta") or {}).get("prompt_hash") != shot_meta.get("prompt_hash"):
                    _add(prompt_errors, sid, "SHOT_PROMPT_STALE", f"{expected.get('id')}: metadata hash stale")

        if not str(scene.get("camera") or "").strip():
            _add(derived_errors, sid, "DERIVED_CAMERA_MISSING", "Camera field is empty.")
        if not str(scene.get("lighting") or "").strip():
            _add(derived_errors, sid, "DERIVED_LIGHTING_MISSING", "Lighting field is empty.")
        if not str(scene.get("atmosphere") or "").strip():
            _add(derived_errors, sid, "DERIVED_ATMOSPHERE_MISSING", "Atmosphere field is empty.")

    errors.extend(duration_errors)
    errors.extend(shot_errors)
    errors.extend(prompt_errors)
    errors.extend(derived_errors)

    gates = {
        "SOURCE_LOCK": not any(x["code"].startswith("SOURCE_LOCK") for x in errors),
        "SOURCE_FIDELITY": not any(x["code"].startswith("SOURCE_") and x["code"] != "SOURCE_SPAN_STALE" for x in errors),
        "CANON_LOCK": not any(x["code"].endswith("_CANON_INCOMPLETE") for x in errors),
        "CHARACTER_LOCK": all(bool(x.get("canonical_locked")) for x in characters),
        "LOCATION_LOCK": all(bool(x.get("canonical_locked")) for x in locations),
        "PROP_STATE": not any("PROP_STATE" in x["code"] or x["code"] == "INVALID_PROP_STATE_TRANSITION" for x in errors),
        "OWNERSHIP": not any("OWNER" in x["code"] for x in errors),
        "EVENT_ORDER": not any(x["code"] in {"DUPLICATE_PROP_TRANSFER", "DUPLICATE_EVENT", "DUPLICATE_SOURCE_BEAT"} for x in errors),
        "START_END": not any(
            x["code"].startswith("START_END")
            or x["code"] in {"PREVIOUS_SCENE_LINK", "LOCATION_TELEPORT", "WEATHER_REGRESSION"}
            for x in errors
        ),
        "DIALOGUE": not any(x["code"].startswith("DIALOGUE") for x in errors),
        "VOICEOVER": not any(x["code"].startswith("VOICEOVER") for x in errors),
        "REFERENTIAL_INTEGRITY": not any(x["code"].startswith("UNKNOWN_") for x in errors),
        "DURATION_BUDGET": not duration_errors,
        "TIMELINE_ORDER": not span_errors,
        "SHOT_PLAN": not any(x["code"] == "SHOT_PLAN_STALE" for x in errors),
        "FLOW_PROMPT_FIDELITY": not prompt_errors,
        "DERIVED_COMPLETENESS": not derived_errors,
    }

    repairable = [x for x in errors if x.get("code") in REPAIRABLE_CODES]
    nonrepairable = [x for x in errors if x.get("code") not in REPAIRABLE_CODES]
    by_scene: dict[str, list[dict]] = {}
    for item in errors + warnings:
        by_scene.setdefault(str(item.get("scene") or "PROJECT"), []).append(item)

    scene_results = []
    for scene in scenes:
        sid = str(scene.get("id") or "")
        scene_issues = by_scene.get(sid, [])
        scene_results.append({
            "scene_id": sid,
            "passed": not any(x in errors for x in scene_issues),
            "issues": scene_issues,
            "repairable": all(x.get("code") in REPAIRABLE_CODES for x in scene_issues if x in errors),
        })

    final_gate = all(gates.values()) and not errors
    return {
        "version": "batch-c-v1",
        "evaluated_at": _now(),
        "final_gate": final_gate,
        "status": "READY_TO_RENDER" if final_gate else "NEEDS_REPAIR",
        "gates": gates,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "repairable_errors": repairable,
        "nonrepairable_errors": nonrepairable,
        "scene_results": scene_results,
        "prop_ledger": prop_ledger,
        "event_ledger": event_ledger,
        "source_spans": spans,
    }


def run_production_gate(project_id: str, persist: bool = True) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    report = evaluate_project_production_gate(project)
    if persist:
        save_production_gate_report(project_id, report)
    return report


def evaluate_production_gate_v2(project_id: str, persist: bool = False) -> dict:
    from .film_production_gate_v2 import evaluate_production_gate_v2 as _evaluate
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    v1 = evaluate_project_production_gate(project)
    return _evaluate(project, v1)


def _default_camera(scene: dict) -> str:
    return "Cinematic coverage faithful to source action; preserve screen direction, scale and spatial continuity."


def _default_lighting(scene: dict, project: dict) -> str:
    loc = next((x for x in (project.get("locations") or []) if x.get("id") == scene.get("location_id")), None)
    if loc and loc.get("lighting"):
        return str(loc["lighting"])
    return "Preserve established location lighting and time-of-day from Canon."


def _default_atmosphere(scene: dict, project: dict) -> str:
    loc = next((x for x in (project.get("locations") or []) if x.get("id") == scene.get("location_id")), None)
    if loc:
        bits = [str(loc.get(k) or "").strip() for k in ("weather", "time_of_day", "colors") if loc.get(k)]
        if bits:
            return " · ".join(bits)
    return "Preserve established environment, weather and ambience from Canon."


def auto_repair_project_derived(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    # Source facts must be valid before any repair. Auto Repair never touches them.
    source_failures = []
    immutable_before = {}
    for scene in project.get("scenes") or []:
        ok, reason = verify_source_lock(scene)
        immutable_before[str(scene.get("id"))] = scene.get("source_hash")
        if not ok:
            source_failures.append({"scene": scene.get("id"), "reason": reason})
    if source_failures:
        raise ValueError(f"SOURCE_LOCK_FAILED: {source_failures[0]}")

    repaired = deepcopy(project)
    changes: list[dict] = []
    scenes = repaired.get("scenes") or []
    settings = repaired.get("settings") or {}
    characters = repaired.get("characters") or []
    locations = repaired.get("locations") or []
    props = repaired.get("props") or []

    spans, _ = compute_source_spans(repaired.get("original_text") or "", scenes)

    for index, scene in enumerate(scenes):
        sid = str(scene.get("id") or "")
        prev = scenes[index - 1] if index > 0 else None
        nxt = scenes[index + 1] if index + 1 < len(scenes) else None

        continuity = deepcopy(scene.get("continuity") or {})
        expected_prev = prev.get("id") if prev else None
        expected_next = nxt.get("id") if nxt else None
        if continuity.get("previous_scene") != expected_prev:
            changes.append({"scene": sid, "field": "continuity.previous_scene", "from": continuity.get("previous_scene"), "to": expected_prev})
            continuity["previous_scene"] = expected_prev
        if continuity.get("next_scene") != expected_next:
            changes.append({"scene": sid, "field": "continuity.next_scene", "from": continuity.get("next_scene"), "to": expected_next})
            continuity["next_scene"] = expected_next
        scene["continuity"] = continuity

        if not str(scene.get("start_state") or "").strip() and prev and str(prev.get("end_state") or "").strip():
            scene["start_state"] = str(prev.get("end_state"))
            changes.append({"scene": sid, "field": "start_state", "to": "copied previous END_STATE"})

        if not str(scene.get("camera") or "").strip():
            scene["camera"] = _default_camera(scene)
            changes.append({"scene": sid, "field": "camera", "to": scene["camera"]})
        if not str(scene.get("lighting") or "").strip():
            scene["lighting"] = _default_lighting(scene, repaired)
            changes.append({"scene": sid, "field": "lighting", "to": scene["lighting"]})
        if not str(scene.get("atmosphere") or "").strip():
            scene["atmosphere"] = _default_atmosphere(scene, repaired)
            changes.append({"scene": sid, "field": "atmosphere", "to": scene["atmosphere"]})

        scene["start_state_structured"] = structure_state(
            scene.get("start_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )
        scene["end_state_structured"] = structure_state(
            scene.get("end_state") or "", scene.get("characters") or [], scene.get("location_id"), scene.get("props_present") or []
        )

        next_span = spans.get(sid) or {}
        if (scene.get("source_span") or {}) != next_span:
            changes.append({"scene": sid, "field": "source_span", "to": next_span})
        scene["source_span"] = next_span

        shots, budget = build_shot_plan(scene, settings)
        if (scene.get("duration_budget") or {}) != budget:
            changes.append({"scene": sid, "field": "duration_budget", "to": budget})
        if [_shot_signature(x) for x in (scene.get("shots") or [])] != [_shot_signature(x) for x in shots]:
            changes.append({"scene": sid, "field": "shot_plan", "to": [x.get("id") for x in shots]})
        scene["duration_budget"] = budget
        scene["shots"] = shots

        prompt, meta = compile_flow_prompt(
            scene,
            repaired.get("visual_style") or "Cinematic",
            characters,
            locations,
            props,
        )
        if scene.get("flow_prompt") != prompt or (scene.get("flow_prompt_meta") or {}).get("prompt_hash") != meta.get("prompt_hash"):
            changes.append({"scene": sid, "field": "flow_prompt", "to": meta.get("prompt_hash")})
        scene["flow_prompt"] = prompt
        scene["flow_prompt_meta"] = meta

        for shot in scene["shots"]:
            shot_prompt, shot_meta = compile_flow_prompt(
                scene,
                repaired.get("visual_style") or "Cinematic",
                characters,
                locations,
                props,
                shot=shot,
            )
            shot["flow_prompt"] = shot_prompt
            shot["flow_prompt_meta"] = shot_meta

    # Verify immutable source fingerprints stayed untouched.
    for scene in scenes:
        sid = str(scene.get("id") or "")
        if scene.get("source_hash") != immutable_before.get(sid):
            raise ValueError(f"SOURCE_MUTATION_BLOCKED:{sid}")
        ok, reason = verify_source_lock(scene)
        if not ok:
            raise ValueError(f"SOURCE_MUTATION_BLOCKED:{sid}:{reason}")

    report = evaluate_project_production_gate(repaired)
    if report.get("final_gate"):
        for scene in scenes:
            warnings = list(scene.get("warnings") or [])
            stale = [
                w for w in warnings
                if str(w).startswith("Thay đổi ở ") and "Hãy kiểm tra START/END STATE" in str(w)
            ]
            if stale:
                scene["warnings"] = [w for w in warnings if w not in stale]
                changes.append({
                    "scene": scene.get("id"),
                    "field": "warnings_cleanup",
                    "removed": len(stale),
                })

    log_entry = {
        "version": "batch-c-v1",
        "repaired_at": _now(),
        "changed_fields": changes,
        "change_count": len(changes),
        "source_mutations": 0,
        "final_gate_after_repair": report.get("final_gate"),
    }
    save_production_repair(project_id, repaired, report, log_entry)
    return get_film_project(project_id)
