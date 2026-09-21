from __future__ import annotations

from .film_boundary_qc import (
    JUNCTION_QC_VERSION,
    build_junction_repair_prompt,
    evaluate_junction_qc,
    junction_repair_target,
    resolve_junction_evidence,
    selected_media_usable,
)
from .film_boundary_store import get_junction, get_pair_junction, list_junctions, mark_junction, upsert_junction
from .film_media_store import get_media, get_selected_media, output_key_for
from .film_scene_state_store import get_active_run, get_ledger, get_scene_state, list_scene_states, upsert_scene_state
from .film_store import get_film_project


def _ordered_scenes(project: dict) -> list[dict]:
    return sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0))


def ensure_junctions(project_id: str) -> list[dict]:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = _ordered_scenes(project)
    items = []
    for prev, nxt in zip(scenes, scenes[1:]):
        row = get_pair_junction(project_id, prev["id"], nxt["id"])
        if not row:
            row = upsert_junction(project_id, prev["id"], nxt["id"], status="PENDING")
        items.append(row)
    return items


def _selected_scene_media(project_id: str, scene_id: str):
    key = output_key_for(role="scene_video", scene_id=scene_id)
    selected = get_selected_media(project_id, key)
    state = get_scene_state(project_id, scene_id) or {}
    if not selected and state.get("selected_media_id"):
        selected = get_media(state["selected_media_id"])
    return selected


def refresh_junction_staleness(project_id: str) -> list[dict]:
    changed = []
    for row in list_junctions(project_id):
        prev = _selected_scene_media(project_id, row["previous_scene_id"])
        nxt = _selected_scene_media(project_id, row["next_scene_id"])
        stale = False
        if row.get("selected_previous_media_id") and prev and prev.get("id") != row.get("selected_previous_media_id"):
            stale = True
        if row.get("selected_next_media_id") and nxt and nxt.get("id") != row.get("selected_next_media_id"):
            stale = True
        if stale and row.get("status") in {"PASS", "FAIL"}:
            changed.append(mark_junction(
                project_id, row["previous_scene_id"], row["next_scene_id"], "STALE",
                error="JUNCTION_STALE: selected media version changed",
            ))
    return changed



def recover_stale_junctions_after_snapshot_rebase(project_id: str) -> dict:
    """Restore only stale PASS junctions whose accepted media/evidence did not change."""
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = {scene["id"]: scene for scene in (project.get("scenes") or [])}
    restored = []
    blocked = []

    for row in list_junctions(project_id):
        if row.get("status") != "STALE":
            continue
        prev_id = row["previous_scene_id"]
        next_id = row["next_scene_id"]
        prev_state = get_scene_state(project_id, prev_id) or {}
        next_state = get_scene_state(project_id, next_id) or {}
        prev_media = _selected_scene_media(project_id, prev_id)
        next_media = _selected_scene_media(project_id, next_id)
        prev_ok, prev_err = selected_media_usable(prev_media, prev_id)
        next_ok, next_err = selected_media_usable(next_media, next_id)
        evidence = resolve_junction_evidence(
            scenes.get(prev_id) or {},
            scenes.get(next_id) or {},
            get_ledger(project_id, prev_id),
            get_ledger(project_id, next_id),
            prev_media,
            next_media,
        )
        qc = row.get("qc") or {}
        reasons = []
        if prev_state.get("status") != "APPROVED":
            reasons.append("PREV_NOT_APPROVED")
        if next_state.get("status") != "APPROVED":
            reasons.append("NEXT_NOT_APPROVED")
        if not prev_ok:
            reasons.append(prev_err or "PREV_MEDIA_INVALID")
        if not next_ok:
            reasons.append(next_err or "NEXT_MEDIA_INVALID")
        if (prev_media or {}).get("id") != row.get("selected_previous_media_id"):
            reasons.append("PREV_MEDIA_CHANGED")
        if (next_media or {}).get("id") != row.get("selected_next_media_id"):
            reasons.append("NEXT_MEDIA_CHANGED")
        if not evidence.get("ok"):
            reasons.append(evidence.get("code") or "EVIDENCE_INVALID")
        if qc.get("passed") is not True:
            reasons.append("OLD_QC_NOT_PASSED")
        if qc.get("version") != JUNCTION_QC_VERSION:
            reasons.append("QC_VERSION_MISMATCH")

        if reasons:
            blocked.append({
                "previous_scene_id": prev_id,
                "next_scene_id": next_id,
                "reasons": reasons,
            })
            continue

        restored_row = mark_junction(
            project_id,
            prev_id,
            next_id,
            "PASS",
            selected_previous_media_id=(prev_media or {}).get("id"),
            selected_next_media_id=(next_media or {}).get("id"),
            qc=qc,
            score=row.get("score"),
            error=None,
        )
        restored.append(restored_row)

    if restored:
        try:
            from .film_event_store import emit_event
            emit_event(
                project_id,
                "JUNCTION_STALE_RECOVERED",
                payload={
                    "restored_count": len(restored),
                    "blocked_count": len(blocked),
                    "method": "deterministic_snapshot_rebase",
                },
            )
        except Exception:
            pass
    return {
        "project_id": project_id,
        "restored": restored,
        "blocked": blocked,
        "restored_count": len(restored),
        "blocked_count": len(blocked),
    }


def _vision_runner(project: dict, previous_scene: dict, next_scene: dict):
    def _run(evidence: dict) -> dict:
        from .film_qc_service import run_junction_vision
        return run_junction_vision(project, previous_scene, next_scene, evidence)
    return _run


def _vision_failed_report(previous_scene_id: str, next_scene_id: str, evidence: dict, exc: Exception) -> dict:
    return {
        "version": JUNCTION_QC_VERSION,
        "passed": False,
        "blocked": True,
        "code": "JUNCTION_VISION_FAILED",
        "overall_score": None,
        "dimensions": {},
        "hard_gate": {"passed": False, "failed": ["vision"], "dimensions": {}},
        "issues": [{"type": "vision", "severity": "critical", "evidence": str(exc)[:800]}],
        "evidence": {
            "previous_scene_id": previous_scene_id,
            "next_scene_id": next_scene_id,
            "previous_last_frame": evidence.get("previous_last_frame"),
            "next_first_frame": evidence.get("next_first_frame"),
            "selected_previous_media_id": evidence.get("selected_previous_media_id"),
            "selected_next_media_id": evidence.get("selected_next_media_id"),
        },
        "vision": None,
    }


def check_junction(project_id: str, previous_scene_id: str, next_scene_id: str, vision_fn=None) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = {scene["id"]: scene for scene in (project.get("scenes") or [])}
    prev = scenes.get(previous_scene_id)
    nxt = scenes.get(next_scene_id)
    if not prev or not nxt:
        raise ValueError("Không tìm thấy scene junction.")
    from .film_event_store import emit_event
    prior_status = (get_pair_junction(project_id, previous_scene_id, next_scene_id) or {}).get("status")
    emit_event(project_id, "JUNCTION_QC_STARTED", scene_id=next_scene_id, payload={"previous_scene_id": previous_scene_id, "next_scene_id": next_scene_id})
    upsert_junction(project_id, previous_scene_id, next_scene_id, status="RUNNING")
    prev_state = get_scene_state(project_id, previous_scene_id) or {}
    next_state = get_scene_state(project_id, next_scene_id) or {}
    if prev_state.get("status") != "APPROVED" or next_state.get("status") != "APPROVED":
        return mark_junction(
            project_id, previous_scene_id, next_scene_id, "PENDING",
            error="JUNCTION_PENDING: cần cả hai scene APPROVED",
            qc={"passed": False, "code": "SCENES_NOT_APPROVED"},
        )
    prev_media = _selected_scene_media(project_id, previous_scene_id)
    next_media = _selected_scene_media(project_id, next_scene_id)
    prev_ledger = get_ledger(project_id, previous_scene_id)
    next_ledger = get_ledger(project_id, next_scene_id)
    evidence = resolve_junction_evidence(prev, nxt, prev_ledger, next_ledger, prev_media, next_media)
    runner = vision_fn
    if evidence.get("ok") and runner is None:
        runner = _vision_runner(project, prev, nxt)
    elif not evidence.get("ok"):
        runner = None
    try:
        report = evaluate_junction_qc(
            prev, nxt,
            previous_ledger=prev_ledger,
            next_ledger=next_ledger,
            previous_media=prev_media,
            next_media=next_media,
            vision_fn=runner,
        )
    except Exception as exc:
        report = _vision_failed_report(previous_scene_id, next_scene_id, evidence, exc)
    if report.get("blocked"):
        status = "BLOCKED"
    else:
        status = "PASS" if report.get("passed") else "FAIL"
    marked = mark_junction(
        project_id, previous_scene_id, next_scene_id, status,
        selected_previous_media_id=(prev_media or {}).get("id"),
        selected_next_media_id=(next_media or {}).get("id"),
        qc=report,
        score=report.get("overall_score"),
        error=None if status == "PASS" else (report.get("code") or ",".join((report.get("hard_gate") or {}).get("failed") or [])),
    )
    emit_event(
        project_id,
        "JUNCTION_QC_PASSED" if status == "PASS" else "JUNCTION_QC_FAILED",
        scene_id=next_scene_id,
        severity="INFO" if status == "PASS" else "ERROR",
        payload={"previous_scene_id": previous_scene_id, "next_scene_id": next_scene_id, "status": status, "score": report.get("overall_score")},
    )
    if prior_status == "REPAIRING":
        emit_event(
            project_id,
            "JUNCTION_REPAIR_COMPLETED",
            scene_id=next_scene_id,
            severity="INFO" if status == "PASS" else "ERROR",
            payload={"previous_scene_id": previous_scene_id, "next_scene_id": next_scene_id, "status": status},
        )
    return marked



async def check_junction_async(project_id: str, previous_scene_id: str, next_scene_id: str, vision_fn=None) -> dict:
    """Async production Junction QC path; never nests an event loop."""
    import inspect

    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = {scene["id"]: scene for scene in (project.get("scenes") or [])}
    prev = scenes.get(previous_scene_id)
    nxt = scenes.get(next_scene_id)
    if not prev or not nxt:
        raise ValueError("Không tìm thấy scene junction.")

    from .film_event_store import emit_event
    prior_status = (get_pair_junction(project_id, previous_scene_id, next_scene_id) or {}).get("status")
    emit_event(project_id, "JUNCTION_QC_STARTED", scene_id=next_scene_id, payload={
        "previous_scene_id": previous_scene_id,
        "next_scene_id": next_scene_id,
    })
    upsert_junction(project_id, previous_scene_id, next_scene_id, status="RUNNING")

    prev_state = get_scene_state(project_id, previous_scene_id) or {}
    next_state = get_scene_state(project_id, next_scene_id) or {}
    if prev_state.get("status") != "APPROVED" or next_state.get("status") != "APPROVED":
        return mark_junction(
            project_id, previous_scene_id, next_scene_id, "PENDING",
            error="JUNCTION_PENDING: cần cả hai scene APPROVED",
            qc={"passed": False, "code": "SCENES_NOT_APPROVED"},
        )

    prev_media = _selected_scene_media(project_id, previous_scene_id)
    next_media = _selected_scene_media(project_id, next_scene_id)
    prev_ledger = get_ledger(project_id, previous_scene_id)
    next_ledger = get_ledger(project_id, next_scene_id)
    evidence = resolve_junction_evidence(prev, nxt, prev_ledger, next_ledger, prev_media, next_media)

    observations = None
    try:
        if evidence.get("ok"):
            if vision_fn is None:
                from .film_junction_vision import run_junction_vision_async
                observations = await run_junction_vision_async(project, prev, nxt, evidence)
            else:
                observations = vision_fn(evidence)
                if inspect.isawaitable(observations):
                    observations = await observations

        report = evaluate_junction_qc(
            prev, nxt,
            previous_ledger=prev_ledger,
            next_ledger=next_ledger,
            previous_media=prev_media,
            next_media=next_media,
            observations=observations,
        )
    except Exception as exc:
        report = _vision_failed_report(previous_scene_id, next_scene_id, evidence, exc)

    if report.get("blocked"):
        status = "BLOCKED"
    else:
        status = "PASS" if report.get("passed") else "FAIL"

    marked = mark_junction(
        project_id, previous_scene_id, next_scene_id, status,
        selected_previous_media_id=(prev_media or {}).get("id"),
        selected_next_media_id=(next_media or {}).get("id"),
        qc=report,
        score=report.get("overall_score"),
        error=None if status == "PASS" else (report.get("code") or ",".join((report.get("hard_gate") or {}).get("failed") or [])),
    )
    emit_event(
        project_id,
        "JUNCTION_QC_PASSED" if status == "PASS" else "JUNCTION_QC_FAILED",
        scene_id=next_scene_id,
        severity="INFO" if status == "PASS" else "ERROR",
        payload={
            "previous_scene_id": previous_scene_id,
            "next_scene_id": next_scene_id,
            "status": status,
            "score": report.get("overall_score"),
        },
    )
    if prior_status == "REPAIRING":
        emit_event(
            project_id,
            "JUNCTION_REPAIR_COMPLETED",
            scene_id=next_scene_id,
            severity="INFO" if status == "PASS" else "ERROR",
            payload={
                "previous_scene_id": previous_scene_id,
                "next_scene_id": next_scene_id,
                "status": status,
            },
        )
    return marked


async def check_project_junctions_async(project_id: str, approved_only: bool = True, vision_fn=None) -> list[dict]:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    ensure_junctions(project_id)
    refresh_junction_staleness(project_id)
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    results = []
    scenes = _ordered_scenes(project)
    for prev, nxt in zip(scenes, scenes[1:]):
        if approved_only:
            if (states.get(prev["id"]) or {}).get("status") != "APPROVED":
                continue
            if (states.get(nxt["id"]) or {}).get("status") != "APPROVED":
                continue
        results.append(await check_junction_async(
            project_id, prev["id"], nxt["id"], vision_fn=vision_fn,
        ))
    return results


async def recheck_junctions_for_scene_async(project_id: str, scene_id: str, vision_fn=None) -> list[dict]:
    ensure_junctions(project_id)
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    updated = []
    for row in list_junctions(project_id):
        if row.get("previous_scene_id") != scene_id and row.get("next_scene_id") != scene_id:
            continue
        prev_status = (states.get(row["previous_scene_id"]) or {}).get("status")
        next_status = (states.get(row["next_scene_id"]) or {}).get("status")
        if prev_status != "APPROVED" or next_status != "APPROVED":
            continue
        updated.append(await check_junction_async(
            project_id, row["previous_scene_id"], row["next_scene_id"], vision_fn=vision_fn,
        ))
    return updated


def check_project_junctions(project_id: str, approved_only: bool = True, vision_fn=None) -> list[dict]:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    ensure_junctions(project_id)
    refresh_junction_staleness(project_id)
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    results = []
    scenes = _ordered_scenes(project)
    for prev, nxt in zip(scenes, scenes[1:]):
        if approved_only:
            if (states.get(prev["id"]) or {}).get("status") != "APPROVED":
                continue
            if (states.get(nxt["id"]) or {}).get("status") != "APPROVED":
                continue
        results.append(check_junction(project_id, prev["id"], nxt["id"], vision_fn=vision_fn))
    return results


def recheck_junctions_for_scene(project_id: str, scene_id: str, vision_fn=None) -> list[dict]:
    ensure_junctions(project_id)
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    updated = []
    for row in list_junctions(project_id):
        if row.get("previous_scene_id") != scene_id and row.get("next_scene_id") != scene_id:
            continue
        prev_status = (states.get(row["previous_scene_id"]) or {}).get("status")
        next_status = (states.get(row["next_scene_id"]) or {}).get("status")
        if prev_status != "APPROVED" or next_status != "APPROVED":
            continue
        updated.append(check_junction(project_id, row["previous_scene_id"], row["next_scene_id"], vision_fn=vision_fn))
    return updated


def retry_junction(project_id: str, junction_id: str) -> dict:
    row = get_junction(project_id, junction_id)
    if not row:
        raise ValueError("Không tìm thấy junction.")
    if row.get("status") == "REPAIRING":
        return row
    project = get_film_project(project_id)
    scenes = {scene["id"]: scene for scene in ((project or {}).get("scenes") or [])}
    prev = scenes.get(row["previous_scene_id"]) or {"id": row["previous_scene_id"]}
    nxt = scenes.get(row["next_scene_id"]) or {"id": row["next_scene_id"]}
    target = junction_repair_target(prev, nxt, get_ledger(project_id, row["previous_scene_id"]))
    prompt = build_junction_repair_prompt(prev, nxt, target, row.get("qc") or {})
    from .film_event_store import emit_event
    emit_event(project_id, "JUNCTION_REPAIR_STARTED", scene_id=row.get("next_scene_id"), payload={"junction_id": row.get("id")})
    updated = mark_junction(
        project_id, row["previous_scene_id"], row["next_scene_id"], "REPAIRING",
        attempt=int(row.get("attempt") or 0) + 1,
        error=target["reason"],
        qc=dict(row.get("qc") or {}, repair=target, repair_prompt=prompt),
    )
    scene_id = target["scene_id"]
    scene = scenes.get(scene_id) or {}
    state = get_scene_state(project_id, scene_id) or {}
    scene_index = int(scene.get("scene_index") or state.get("scene_index") or 0)
    snapshot = dict(state.get("snapshot") or {})
    snapshot["junction_repair"] = target
    snapshot["junction_repair_prompt"] = prompt
    snapshot["junction_id"] = row.get("id")
    upsert_scene_state(
        project_id, scene_id, scene_index,
        status="REGENERATING",
        attempt=int(state.get("attempt") or 0) + 1,
        current_job_id=None,
        error=None,
        blocked_reason=target["reason"],
        snapshot=snapshot,
        force=True,
    )
    from .film_pipeline_service import resume_pipeline, start_pipeline
    active = get_active_run(project_id)
    if active and active.get("status") == "paused":
        pipeline = resume_pipeline(project_id)
    elif active and active.get("status") in {"running", "stopping"}:
        pipeline = {"run": active, "reused_active_run": True}
    else:
        pipeline = start_pipeline(project_id, from_scene_id=scene_id, scene_limit=1)
    updated["repair"] = target
    updated["repair_target_scene"] = scene_id
    updated["pipeline"] = pipeline
    updated["pipeline_run_id"] = ((pipeline or {}).get("run") or {}).get("id")
    updated["status"] = "REPAIRING"
    return updated


def list_project_junctions(project_id: str) -> dict:
    ensure_junctions(project_id)
    refresh_junction_staleness(project_id)
    items = list_junctions(project_id)
    by_status: dict[str, int] = {}
    for item in items:
        key = str(item.get("status") or "PENDING")
        by_status[key] = by_status.get(key, 0) + 1
    return {"project_id": project_id, "junctions": items, "counts": by_status}
