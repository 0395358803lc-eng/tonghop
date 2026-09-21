from __future__ import annotations

from datetime import datetime, timezone

from .film_acceptance_snapshot import (
    create_acceptance_snapshot,
    propagate_scene_change,
    snapshot_fingerprint_mismatch,
    validate_approval_for_snapshot,
)
from .film_audio_schema import normalize_audio_requirements, speech_required
from .film_compiler import compile_flow_prompt
from .film_integrity import verify_source_lock
from .film_pipeline_service import pipeline_worker_active
from .film_production_gate import auto_repair_project_derived, run_production_gate
from .film_scene_state_store import get_scene_state
from .film_speaker_acceptance import run_project_speaker_acceptance
from .film_store import append_repair_log, get_film_project

POLICY_VERSION = "deterministic_flow_prompt_v4_narrator_lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_policy_revalidation(project: dict, acceptance: dict) -> dict[str, dict]:
    items = {
        str(item.get("scene_id")): item
        for item in (acceptance.get("items") or [])
        if isinstance(item, dict) and item.get("scene_id")
    }
    result: dict[str, dict] = {}
    for scene in project.get("scenes") or []:
        sid = str(scene.get("id") or "")
        req = normalize_audio_requirements(scene, project)
        if not speech_required(scene):
            result[sid] = {
                "policy": POLICY_VERSION,
                "passed": True,
                "method": "no_speech_policy_only",
                "speech_required": False,
            }
            continue
        item = items.get(sid)
        if not item:
            result[sid] = {
                "policy": POLICY_VERSION,
                "passed": False,
                "method": "speaker_acceptance",
                "speech_required": True,
                "reason": "SPEAKER_ACCEPTANCE_MISSING",
            }
            continue
        passed = bool(item.get("passed")) and item.get("stt_passed") is True
        result[sid] = {
            "policy": POLICY_VERSION,
            "passed": passed,
            "method": "speaker_acceptance",
            "speech_required": True,
            "expected_speakers": req.get("speakers") or [],
            "speaker_character_id": item.get("speaker_character_id"),
            "speaker_status": item.get("status"),
            "speaker_similarity": item.get("speaker_similarity"),
            "threshold": item.get("threshold"),
            "threshold_source": item.get("threshold_source"),
            "stt_passed": item.get("stt_passed"),
            "stt_match_score": item.get("stt_match_score"),
            "reference_scene_id": item.get("reference_scene_id"),
            "reason": None if passed else (
                item.get("error")
                or f"SPEAKER_POLICY_FAILED:{item.get('status') or 'not_evaluated'}"
            ),
        }
    return result


def build_prompt_policy_migration_plan(
    project_id: str,
    acceptance: dict | None = None,
) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")

    source_failures = []
    source_hashes: dict[str, str | None] = {}
    for scene in project.get("scenes") or []:
        sid = str(scene.get("id") or "")
        source_hashes[sid] = scene.get("source_hash")
        ok, reason = verify_source_lock(scene)
        if not ok:
            source_failures.append({"scene_id": sid, "reason": reason})

    if source_failures:
        return {
            "project_id": project_id,
            "policy": POLICY_VERSION,
            "blocked": True,
            "reason": "SOURCE_LOCK_FAILED",
            "source_failures": source_failures,
            "rows": [],
            "source_hashes": source_hashes,
        }

    acceptance = acceptance if acceptance is not None else run_project_speaker_acceptance(project_id)
    policy = build_policy_revalidation(project, acceptance)
    characters = project.get("characters") or []
    locations = project.get("locations") or []
    props = project.get("props") or []

    rows = []
    for scene in sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0)):
        sid = str(scene.get("id") or "")
        old_prompt = str(scene.get("flow_prompt") or "")
        old_meta = scene.get("flow_prompt_meta") or {}
        new_prompt, new_meta = compile_flow_prompt(
            scene,
            project.get("visual_style") or "Cinematic",
            characters,
            locations,
            props,
        )

        state = get_scene_state(project_id, sid) or {}
        approval_ok = False
        approval_reason = "SCENE_NOT_APPROVED"
        if state.get("status") == "APPROVED":
            approval_ok, approval_reason = validate_approval_for_snapshot(project_id, sid)

        scene_policy = policy.get(sid) or {
            "policy": POLICY_VERSION,
            "passed": False,
            "reason": "POLICY_REVALIDATION_MISSING",
        }
        keep_media = bool(scene_policy.get("passed")) and bool(approval_ok)
        action = "KEEP_MEDIA_REBASE" if keep_media else "STALE_RERENDER"

        rows.append({
            "scene_id": sid,
            "scene_index": int(scene.get("scene_index") or 0),
            "status": state.get("status"),
            "selected_media_id": state.get("selected_media_id"),
            "old_compiler": old_meta.get("compiler"),
            "new_compiler": new_meta.get("compiler"),
            "old_prompt_hash": old_meta.get("prompt_hash"),
            "new_prompt_hash": new_meta.get("prompt_hash"),
            "prompt_changed": old_prompt != new_prompt,
            "approval_ok": bool(approval_ok),
            "approval_reason": approval_reason,
            "policy": scene_policy,
            "action": action,
            "reason": None if keep_media else (
                scene_policy.get("reason")
                or approval_reason
                or "PROMPT_POLICY_REVALIDATION_FAILED"
            ),
        })

    return {
        "project_id": project_id,
        "policy": POLICY_VERSION,
        "blocked": False,
        "source_failures": [],
        "source_hashes": source_hashes,
        "rows": rows,
        "keep_media_count": sum(1 for row in rows if row["action"] == "KEEP_MEDIA_REBASE"),
        "stale_rerender_count": sum(1 for row in rows if row["action"] == "STALE_RERENDER"),
    }


def apply_prompt_policy_migration(
    project_id: str,
    acceptance: dict | None = None,
) -> dict:
    if pipeline_worker_active(project_id):
        raise ValueError("PIPELINE_ACTIVE: không migration prompt khi pipeline đang chạy.")

    plan = build_prompt_policy_migration_plan(project_id, acceptance=acceptance)
    if plan.get("blocked"):
        raise ValueError(f"PROMPT_POLICY_MIGRATION_BLOCKED: {plan.get('reason')}")

    repaired = auto_repair_project_derived(project_id)
    if not repaired:
        raise ValueError("PROMPT_POLICY_MIGRATION_FAILED: auto repair không trả project.")

    expected_hashes = plan.get("source_hashes") or {}
    for scene in repaired.get("scenes") or []:
        sid = str(scene.get("id") or "")
        if scene.get("source_hash") != expected_hashes.get(sid):
            raise ValueError(f"SOURCE_MUTATION_BLOCKED:{sid}")
        ok, reason = verify_source_lock(scene)
        if not ok:
            raise ValueError(f"SOURCE_MUTATION_BLOCKED:{sid}:{reason}")

    gate = run_production_gate(project_id, persist=True)
    if not gate.get("final_gate"):
        raise ValueError("PROMPT_POLICY_MIGRATION_GATE_FAILED")

    rebased = []
    stale = []
    for row in plan.get("rows") or []:
        sid = row["scene_id"]
        if row["action"] == "KEEP_MEDIA_REBASE":
            snapshot = create_acceptance_snapshot(
                project_id,
                sid,
                policy_revalidation=row.get("policy") or {},
            )
            rebased.append({
                "scene_id": sid,
                "snapshot_id": (snapshot or {}).get("id"),
                "snapshot_hash": (snapshot or {}).get("snapshot_hash"),
            })
        else:
            reason = f"PROMPT_POLICY_STALE:{row.get('reason') or 'revalidation failed'}"
            stale.append({
                "scene_id": sid,
                "result": propagate_scene_change(project_id, sid, reason),
                "reason": reason,
            })

    mismatches = [
        row["scene_id"]
        for row in plan.get("rows") or []
        if row["action"] == "KEEP_MEDIA_REBASE"
        and snapshot_fingerprint_mismatch(project_id, row["scene_id"])
    ]
    if mismatches:
        raise ValueError(
            "PROMPT_POLICY_SNAPSHOT_REBASE_FAILED:" + ",".join(mismatches)
        )

    log_entry = {
        "version": "prompt-policy-migration-v1",
        "policy": POLICY_VERSION,
        "migrated_at": _now(),
        "keep_media_count": len(rebased),
        "stale_rerender_count": len(stale),
        "rebased_scenes": [item["scene_id"] for item in rebased],
        "stale_scenes": [item["scene_id"] for item in stale],
        "source_mutations": 0,
        "production_gate_after_migration": bool(gate.get("final_gate")),
    }
    append_repair_log(project_id, log_entry)

    return {
        "project_id": project_id,
        "policy": POLICY_VERSION,
        "production_gate": gate,
        "plan": plan,
        "rebased": rebased,
        "stale": stale,
        "snapshot_mismatches": mismatches,
        "source_mutations": 0,
    }
