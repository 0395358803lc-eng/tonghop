from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .film_media_store import get_selected_media, output_key_for
from .film_qc_service import get_qc_status
from .film_render_adapters import get_adapter_status
from .film_resource_store import list_project_resources, scene_resource_manifest
from .film_scene_state_store import list_scene_states
from .flow_bridge_client import _credentials, _headers
from .flow_store import get_flow_status


def _add(errors: list[dict], scene: str, code: str, detail: str) -> None:
    errors.append({"scene": scene, "code": code, "detail": detail})


def _probe_flow() -> dict:
    status = get_flow_status()
    adapter = get_adapter_status()
    result = {
        "configured": bool(status.get("configured") and status.get("enabled") and adapter.get("configured")),
        "authenticated": False,
        "video_available": False,
        "max_references": None,
        "error": None,
    }
    if not result["configured"]:
        result["error"] = "FLOW: video capability unavailable"
        return result
    try:
        base_url, api_key = _credentials()
        with httpx.Client(timeout=20.0) as client:
            session = client.get(f"{base_url}/v1/session", headers=_headers(api_key))
            session.raise_for_status()
            session_data = session.json()
            state = str(session_data.get("state") or "")
            url = str(session_data.get("url") or "")
            usable = bool(session_data.get("authenticated")) and state == "AUTHENTICATED" and session_data.get("project_usable", True) is not False
            if "/404" in url and "reason=project" in url:
                usable = False
                state = state or "PROJECT_NOT_FOUND"
            result["authenticated"] = usable
            result["state"] = state or ("AUTHENTICATED" if usable else "REAUTH_REQUIRED")
            result["url"] = url
            caps = client.get(f"{base_url}/v1/capabilities/video", headers=_headers(api_key))
            if caps.status_code < 400:
                data = caps.json()
                result["video_available"] = bool(
                    data.get("ok", True)
                    and (data.get("supports_video", True) if "supports_video" in data else True)
                    and (data.get("models") or data.get("durations") or data)
                )
                result["max_references"] = data.get("max_references") or (data.get("ingredients") or {}).get("max")
            else:
                result["video_available"] = result["authenticated"]
    except Exception as exc:
        result["error"] = f"FLOW: {exc}"[:500]
        result["authenticated"] = False
        result["video_available"] = False
    if not result["authenticated"]:
        result["error"] = result["error"] or "FLOW: not authenticated"
    if result["authenticated"] and not result["video_available"]:
        result["error"] = "FLOW: video capability unavailable"
    return result


def evaluate_production_gate_v2(project: dict, v1: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    scenes = sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0))
    characters = project.get("characters") or []
    locations = project.get("locations") or []
    props = project.get("props") or []
    project_id = str(project.get("id") or "")

    if not (project.get("story_bible") or project.get("character_bible") or characters):
        _add(errors, "PROJECT", "STORY_BIBLE_MISSING", "Story Bible locked: missing")
    if project.get("status") not in {"ready", "completed"} and not v1.get("final_gate"):
        _add(errors, "PROJECT", "STORY_BIBLE_NOT_LOCKED", f"Story Bible/project status={project.get('status')}")

    indexes = [int(scene.get("scene_index") or 0) for scene in scenes]
    if indexes and indexes != list(range(min(indexes), min(indexes) + len(indexes))):
        _add(errors, "PROJECT", "SCENE_ORDER_INVALID", f"Scene ordering invalid: {indexes}")
    if len(indexes) != len(set(indexes)):
        _add(errors, "PROJECT", "SCENE_ORDER_INVALID", "Duplicate scene_index")
    for i, scene in enumerate(scenes):
        sid = str(scene.get("id") or "UNKNOWN")
        expected_prev = scenes[i - 1].get("id") if i else None
        linked = (scene.get("continuity") or {}).get("previous_scene")
        if i and linked not in {None, expected_prev}:
            _add(errors, sid, "SCENE_DEPENDENCY_INVALID", f"previous_scene={linked} expected={expected_prev}")

    resources = list_project_resources(project_id, "flow") if project_id else []
    active = [x for x in resources if x.get("status") != "retired"]
    by_key = {(x.get("resource_type"), x.get("entity_id")): x for x in active}

    def _canonical_ok(kind: str, entity_id: str, label: str) -> None:
        row = by_key.get((kind, str(entity_id)))
        if not row:
            _add(errors, label, f"{label}: canonical media missing", f"{label}: canonical media missing")
            return
        if row.get("status") == "stale":
            _add(errors, label, f"{label}: selected media stale", f"{label}: selected media stale")
            return
        metadata = row.get("metadata") or {}
        qc = metadata.get("canonical_qc") if isinstance(metadata.get("canonical_qc"), dict) else {}
        if not bool((qc.get("hard_gate") or {}).get("passed") is True):
            _add(errors, label, f"{label}: canonical QC not passed", f"{label}: canonical QC not passed")
        selected = None
        if project_id:
            selected = get_selected_media(project_id, output_key_for(role="canonical_image", resource_type=kind, entity_id=str(entity_id)))
        if selected and selected.get("qc_status") != "passed":
            _add(errors, label, f"{label}: selected media stale", f"{label}: selected media not QC passed")
        if row.get("status") not in {"ready", "locked"} or not row.get("local_path"):
            _add(errors, label, f"{label}: canonical media missing", f"{label}: canonical media missing")

    for item in characters:
        cid = str(item.get("id") or "")
        if cid:
            _canonical_ok("character", cid, cid)
        if item.get("canonical_locked") is False:
            _add(errors, cid or "CHAR", f"{cid}: not locked", f"{cid}: canonical not locked")
    for item in locations:
        lid = str(item.get("id") or "")
        if lid:
            _canonical_ok("location", lid, lid)
    for item in props:
        pid = str(item.get("id") or "")
        if pid:
            _canonical_ok("prop", pid, pid)

    for scene in scenes:
        sid = str(scene.get("id") or "UNKNOWN")
        manifest = scene_resource_manifest(project, scene, "flow")
        if not manifest.get("ready"):
            for miss in manifest.get("missing") or ["reference manifest incomplete"]:
                _add(errors, sid, "REFERENCE_MANIFEST_INCOMPLETE", f"{sid}: {miss}")

    for item in v1.get("errors") or []:
        if isinstance(item, dict):
            _add(errors, str(item.get("scene") or "PROJECT"), str(item.get("code") or "V1"), str(item.get("detail") or item.get("code")))

    flow = _probe_flow()
    if not flow.get("authenticated"):
        _add(errors, "FLOW", "FLOW_NOT_AUTHENTICATED", flow.get("error") or "FLOW: not authenticated")
    if not flow.get("video_available"):
        _add(errors, "FLOW", "FLOW_VIDEO_UNAVAILABLE", "FLOW: video capability unavailable")

    qc = get_qc_status()
    if not qc.get("configured"):
        _add(errors, "QC", "QC_PROVIDER_UNAVAILABLE", "QC provider available: false")

    for state in list_scene_states(project_id) if project_id else []:
        if state.get("status") == "STALE":
            _add(warnings, state.get("scene_id") or "SCENE", "SCENE_STALE", f"{state.get('scene_id')}: canonical/story changed")

    unique = []
    seen = set()
    for item in errors:
        key = (item.get("scene"), item.get("code"), item.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    checks = {
        "story_bible_locked": not any(x["code"] in {"STORY_BIBLE_MISSING", "STORY_BIBLE_NOT_LOCKED"} for x in unique),
        "scenes_locked": not any(str(x["code"]).startswith("SOURCE_LOCK") for x in unique),
        "canonical_complete": not any("canonical media missing" in x["detail"] for x in unique),
        "canonical_qc_pass": not any("canonical QC not passed" in x["detail"] for x in unique),
        "canonical_selected_valid": not any("selected media" in x["detail"] for x in unique),
        "no_stale_canonical": not any("stale" in x["detail"] for x in unique),
        "flow_authenticated": bool(flow.get("authenticated")),
        "flow_video_available": bool(flow.get("video_available")),
        "qc_provider_available": bool(qc.get("configured")),
        "reference_manifest_complete": not any(x["code"] == "REFERENCE_MANIFEST_INCOMPLETE" for x in unique),
        "scene_ordering_valid": not any(x["code"] == "SCENE_ORDER_INVALID" for x in unique),
        "scene_dependencies_valid": not any(x["code"] == "SCENE_DEPENDENCY_INVALID" for x in unique),
        "no_unresolved_continuity": not any(str(x.get("code") or "").startswith(("START_END", "PREVIOUS_SCENE_LINK", "LOCATION_TELEPORT")) for x in unique),
        "v1_final_gate": bool(v1.get("final_gate")),
        "event_store_available": True,
        "no_orphan_jobs": True,
        "recovery_state_clean": True,
        "capability_matrix_fresh": True,
        "snapshot_baseline_complete": True,
    }
    try:
        from .film_event_store import event_store_available
        from .film_recovery_service import detect_orphan_jobs, recovery_state_clean
        from .film_capability_matrix import matrix_is_fresh
        from .film_acceptance_snapshot import list_acceptance_snapshots
        checks["event_store_available"] = event_store_available()
        checks["no_orphan_jobs"] = not bool(detect_orphan_jobs(project_id) if project_id else [])
        checks["recovery_state_clean"] = recovery_state_clean(project_id) if project_id else True
        checks["capability_matrix_fresh"] = matrix_is_fresh("video")
        approved = [s for s in (list_scene_states(project_id) if project_id else []) if s.get("status") == "APPROVED"]
        missing_snaps = [s["scene_id"] for s in approved if not list_acceptance_snapshots(project_id, s["scene_id"])]
        checks["snapshot_baseline_complete"] = not missing_snaps
        if not checks["event_store_available"]:
            _add(warnings, "OPS", "EVENT_STORE_UNAVAILABLE", "event store unavailable")
        if not checks["no_orphan_jobs"]:
            _add(warnings, "OPS", "ORPHAN_JOBS", "orphan render jobs detected")
        if not checks["recovery_state_clean"]:
            _add(warnings, "OPS", "RECOVERY_DIRTY", "recovery state not clean")
        if not checks["capability_matrix_fresh"]:
            _add(warnings, "FLOW", "CAPABILITY_STALE", "capability matrix missing or stale")
        if missing_snaps:
            _add(warnings, "SNAPSHOT", "SNAPSHOT_BASELINE_INCOMPLETE", ",".join(missing_snaps[:8]))
    except Exception:
        pass
    final_gate = not unique and bool(v1.get("final_gate"))
    return {
        "version": "production-gate-v2",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "final_gate": final_gate,
        "status": "READY_TO_RENDER" if final_gate else "NEEDS_REPAIR",
        "checks": checks,
        "errors": unique,
        "warnings": warnings,
        "v1": {"final_gate": v1.get("final_gate"), "error_count": v1.get("error_count"), "status": v1.get("status")},
        "flow": {k: flow.get(k) for k in ("configured", "authenticated", "video_available", "max_references")},
        "qc": {"configured": bool(qc.get("configured")), "id": qc.get("id")},
    }
