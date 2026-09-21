from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from .db import connect, init_db
from .film_acceptance_snapshot import backfill_acceptance_snapshots
from .film_boundary_store import get_pair_junction, upsert_junction
from .film_boundary_service import retry_junction
from .film_capability_matrix import (
    canonical_model_name,
    evaluate_capability,
    ingest_capability_payload,
    matrix_is_fresh,
    refresh_capability_matrix,
    select_model,
    upsert_capability,
)
from .film_event_store import emit_event, event_store_available, list_events
from .film_final_assembly import assemble_project
from .film_final_store import create_final_render
from .film_observability_service import project_metrics
from .film_pipeline_service import pause_pipeline, resume_pipeline, retry_scene, start_pipeline
from .film_recovery_service import (
    classify_render_job,
    detect_orphan_jobs,
    reclaim_expired_lease,
    reconcile_project,
)
from .film_render_store import update_render_job
from .film_scene_state_store import (
    acquire_execution_lease,
    append_run_log,
    create_run,
    ensure_scene_states,
    get_scene_state,
    upsert_scene_state,
)
from .film_store import create_film_project, delete_film_project


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _insert_job(project_id, scene_id, **values):
    job_id = values.get("id") or str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO film_render_jobs(
                 id,project_id,scene_id,scene_index,adapter,status,progress,attempt,prompt,
                 reference_json,error,provider_job_id,result_url,qc_status,provider_error_code,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, project_id, scene_id, int(values.get("scene_index") or 0),
                values.get("adapter") or "flow", values.get("status") or "generating",
                int(values.get("progress") or 0), int(values.get("attempt") or 0),
                values.get("prompt") or "p", "{}", values.get("error"),
                values.get("provider_job_id"), values.get("result_url"),
                values.get("qc_status") or "not_run", values.get("provider_error_code"),
                values.get("updated_at") or _now_iso(),
            ),
        )
    return job_id


def _count(sql, args=()):
    with connect() as conn:
        return conn.execute(sql, args).fetchone()[0]


class Batch4Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.project = create_film_project(
            "__batch4_hardening__",
            "Day la kich ban kiem thu observability recovery capability dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        cls.pid = cls.project["id"]
        stubs = [
            ("SCENE_E", 0), ("SCENE_G", 1), ("SCENE_R", 2), ("S1", 3), ("S2", 4),
        ]
        with connect() as conn:
            for sid, idx in stubs:
                row = conn.execute("SELECT id FROM film_scenes WHERE project_id=? AND id=?", (cls.pid, sid)).fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO film_scenes(id, project_id, scene_index, title, source_text) VALUES(?,?,?,?,?)",
                        (sid, cls.pid, idx, sid, "batch4 stub scene"),
                    )
        ensure_scene_states(cls.pid, [{"id": sid, "scene_index": idx} for sid, idx in stubs])

    @classmethod
    def tearDownClass(cls):
        delete_film_project(cls.project["id"])
        with connect() as conn:
            conn.execute("DELETE FROM film_capability_matrix WHERE model LIKE '__B4%' OR model LIKE 'Veo 3.1 - Lite B4%'")


class ObservabilityTests(Batch4Case):
    def test_event_store_available(self):
        self.assertTrue(event_store_available())

    def test_event_written_for_scene_generation(self):
        upsert_scene_state(self.pid, "SCENE_E", 0, status="QUEUED", force=True)
        upsert_scene_state(self.pid, "SCENE_E", 0, status="GENERATING", current_job_id="job-gen", force=True)
        types = [item["event_type"] for item in list_events(self.pid, scene_id="SCENE_E")]
        self.assertIn("SCENE_QUEUED", types)
        self.assertIn("SCENE_GENERATING", types)

    def test_event_written_for_qc_fail(self):
        upsert_scene_state(self.pid, "SCENE_E", 0, status="QC_RUNNING", current_job_id="job-qc", force=True)
        upsert_scene_state(self.pid, "SCENE_E", 0, status="QC_FAILED", error="identity fail", force=True)
        types = [item["event_type"] for item in list_events(self.pid, scene_id="SCENE_E")]
        self.assertIn("SCENE_QC_FAILED", types)
        self.assertIn("VIDEO_QC_FAILED", types)

    def test_event_written_for_retry(self):
        run = create_run(self.pid, gate={"final_gate": True})
        append_run_log(run["id"], {"action": "retry_scene", "scene_id": "SCENE_E", "job_id": "job-r"})
        types = [item["event_type"] for item in list_events(self.pid, run_id=run["id"])]
        self.assertIn("SCENE_REGENERATING", types)

    def test_event_written_for_flow_timeout(self):
        jid = _insert_job(self.pid, "SCENE_E", status="generating")
        update_render_job(jid, status="failed", provider_error_code="GENERATION_TIMEOUT", error="GENERATION_TIMEOUT: locator")
        types = [item["event_type"] for item in list_events(self.pid, scene_id="SCENE_E") if item.get("job_id") == jid]
        self.assertIn("FLOW_JOB_TIMEOUT", types)

    def test_event_ordering(self):
        upsert_scene_state(self.pid, "SCENE_R", 2, status="QUEUED", force=True)
        upsert_scene_state(self.pid, "SCENE_R", 2, status="GENERATING", force=True)
        upsert_scene_state(self.pid, "SCENE_R", 2, status="QC_RUNNING", force=True)
        ordered = [item["event_type"] for item in list_events(self.pid, scene_id="SCENE_R")]
        self.assertLess(ordered.index("SCENE_QUEUED"), ordered.index("SCENE_GENERATING"))
        self.assertLess(ordered.index("SCENE_GENERATING"), ordered.index("SCENE_QC_RUNNING"))

    def test_event_correlation_ids(self):
        emit_event(self.pid, "FLOW_JOB_STARTED", run_id="run-x", scene_id="SCENE_E", job_id="job-x", payload={"provider_job_id": "prov-1"})
        row = list_events(self.pid, scene_id="SCENE_E", event_type="FLOW_JOB_STARTED")[-1]
        self.assertEqual(row["project_id"], self.pid)
        self.assertEqual(row["run_id"], "run-x")
        self.assertEqual(row["scene_id"], "SCENE_E")
        self.assertEqual(row["job_id"], "job-x")
        self.assertEqual((row.get("payload") or {}).get("provider_job_id"), "prov-1")
        metrics = project_metrics(self.pid)
        self.assertGreaterEqual(metrics["event_count"], 1)


class RecoveryTests(Batch4Case):
    def test_restart_during_generation_recovers(self):
        upsert_scene_state(self.pid, "SCENE_G", 1, status="GENERATING", force=True)
        jid = _insert_job(self.pid, "SCENE_G", scene_index=1, status="completed", result_url="http://local/result.mp4", qc_status="not_run")
        upsert_scene_state(self.pid, "SCENE_G", 1, current_job_id=jid, force=True)
        report = reconcile_project(self.pid)
        self.assertEqual(get_scene_state(self.pid, "SCENE_G")["status"], "QC_RUNNING")
        self.assertTrue(any(item.get("action") == "continue_qc" for item in report.get("recovered") or []))

    def test_restart_after_provider_complete_before_db_update(self):
        job = {"status": "completed", "result_url": "http://x/a.mp4", "qc_status": "not_run"}
        self.assertEqual(classify_render_job(job, False), "completed_externally")
        upsert_scene_state(self.pid, "SCENE_G", 1, status="GENERATING", force=True)
        jid = _insert_job(self.pid, "SCENE_G", scene_index=1, status="completed", result_url="http://x/a.mp4")
        upsert_scene_state(self.pid, "SCENE_G", 1, current_job_id=jid, force=True)
        before = _count("SELECT COUNT(*) FROM film_render_jobs WHERE project_id=? AND scene_id=?", (self.pid, "SCENE_G"))
        reconcile_project(self.pid)
        after = _count("SELECT COUNT(*) FROM film_render_jobs WHERE project_id=? AND scene_id=?", (self.pid, "SCENE_G"))
        self.assertEqual(before, after)
        self.assertEqual(get_scene_state(self.pid, "SCENE_G")["current_job_id"], jid)

    def test_expired_worker_lease_reclaimed(self):
        acquire_execution_lease(self.pid, worker_id="worker-old", ttl=30)
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with connect() as conn:
            conn.execute("UPDATE film_pipeline_execution_leases SET lease_until=? WHERE project_id=?", (past, self.pid))
        out = reclaim_expired_lease(self.pid)
        self.assertTrue(out["reclaimed"])
        self.assertTrue(list_events(self.pid, event_type="LEASE_RECLAIMED"))

    def test_active_lease_not_duplicated(self):
        first = acquire_execution_lease(self.pid, worker_id="worker-a", ttl=60)
        second = acquire_execution_lease(self.pid, worker_id="worker-b", ttl=60)
        self.assertEqual(first["worker_id"], "worker-a")
        self.assertEqual(second["worker_id"], "worker-a")

    def test_orphan_render_job_detected(self):
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        jid = _insert_job(self.pid, "SCENE_R", scene_index=2, status="generating", provider_job_id=None, updated_at=stale)
        orphans = detect_orphan_jobs(self.pid)
        ids = {item.get("id") for item in orphans}
        self.assertIn(jid, ids)
        self.assertTrue(list_events(self.pid, event_type="ORPHAN_JOB_DETECTED"))

    def test_dependency_blocked_job_is_not_orphan(self):
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        job = {
            "status": "generating",
            "provider_job_id": None,
            "provider_error_code": "SESSION_EXPIRED",
            "updated_at": stale,
        }
        self.assertEqual(classify_render_job(job, False), "dependency_blocked")
        jid = _insert_job(
            self.pid,
            "SCENE_R",
            scene_index=2,
            status="generating",
            provider_job_id=None,
            provider_error_code="SESSION_EXPIRED",
            updated_at=stale,
        )
        ids = {item.get("id") for item in detect_orphan_jobs(self.pid)}
        self.assertNotIn(jid, ids)

    def test_recovery_does_not_duplicate_media(self):
        before = _count("SELECT COUNT(*) FROM film_generated_media WHERE project_id=?", (self.pid,))
        reconcile_project(self.pid)
        after = _count("SELECT COUNT(*) FROM film_generated_media WHERE project_id=?", (self.pid,))
        self.assertEqual(before, after)


class IdempotencyTests(Batch4Case):
    def test_double_start_is_idempotent(self):
        scenes = [{"id": "SCENE_E", "scene_index": 0, "duration": 8}]
        fake = dict(self.project)
        fake["scenes"] = scenes
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            a = start_pipeline(self.pid)
            b = start_pipeline(self.pid)
        self.assertEqual(a["run"]["id"], b["run"]["id"])

    def test_start_pipeline_preserves_existing_attempt(self):
        scenes = [{"id": "SCENE_ATTEMPT_KEEP", "scene_index": 0, "duration": 8}]
        fake = dict(self.project)
        fake["scenes"] = scenes
        upsert_scene_state(
            self.pid,
            "SCENE_ATTEMPT_KEEP",
            0,
            status="QUEUED",
            attempt=3,
            current_job_id=None,
            error=None,
            blocked_reason=None,
            force=True,
        )
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            out = start_pipeline(self.pid, from_scene_id="SCENE_ATTEMPT_KEEP", scene_limit=1)
        state = get_scene_state(self.pid, "SCENE_ATTEMPT_KEEP")
        self.assertEqual(state["attempt"], 3)
        self.assertEqual(state["status"], "QUEUED")
        self.assertEqual(out["run"]["from_scene_id"], "SCENE_ATTEMPT_KEEP")

    def test_double_resume_is_idempotent(self):
        create_run(self.pid, gate={"final_gate": True})
        pause_pipeline(self.pid)
        a = resume_pipeline(self.pid)
        b = resume_pipeline(self.pid)
        self.assertEqual(a["run"]["id"], b["run"]["id"])
        self.assertEqual(b["run"]["status"], "running")

    def test_double_retry_scene_is_idempotent(self):
        upsert_scene_state(self.pid, "SCENE_E", 0, status="QC_FAILED", error="QC V2 failed identity", force=True)
        fake = dict(self.project)
        fake["scenes"] = [{"id": "SCENE_E", "scene_index": 0}]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.start_pipeline", return_value={"run": {"id": "run-retry"}}
        ):
            a = retry_scene(self.pid, "SCENE_E")
            b = retry_scene(self.pid, "SCENE_E")
        self.assertEqual(get_scene_state(self.pid, "SCENE_E")["status"], "REGENERATING")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)

    def test_blocked_operator_retry_increments_attempt_once(self):
        upsert_scene_state(
            self.pid,
            "SCENE_E",
            0,
            status="BLOCKED",
            attempt=2,
            current_job_id="old-terminal-job",
            error="FLOW_RUNTIME_ERROR: Flow job timeout",
            blocked_reason="FLOW_RUNTIME_ERROR: Flow job timeout",
            force=True,
        )
        fake = dict(self.project)
        fake["scenes"] = [{"id": "SCENE_E", "scene_index": 0}]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.start_pipeline", return_value={"run": {"id": "run-operator-retry"}}
        ):
            first = retry_scene(self.pid, "SCENE_E")
            after_first = get_scene_state(self.pid, "SCENE_E")
            second = retry_scene(self.pid, "SCENE_E")
            after_second = get_scene_state(self.pid, "SCENE_E")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(after_first["status"], "QUEUED")
        self.assertEqual(after_first["attempt"], 3)
        self.assertIsNone(after_first["current_job_id"])
        self.assertEqual(after_second["attempt"], 3)

    def test_blocked_qc_operator_retry_stores_repair_prompt(self):
        qc_report = {
            "dimensions": {"prop": {"passed": False, "score": 35.0}},
            "hard_gate": {"failed": ["prop"]},
            "issues": [
                {
                    "type": "prop",
                    "severity": "critical",
                    "expected": "PROP_002 must remain the canonical brown leather watch with silver case and hands frozen at 7:00.",
                }
            ],
        }
        upsert_scene_state(
            self.pid,
            "SCENE_E",
            0,
            status="BLOCKED",
            attempt=4,
            current_job_id=None,
            error="QC V2 failed score=42.0 hard=prop",
            blocked_reason="QC V2 failed score=42.0 hard=prop",
            qc=qc_report,
            snapshot={"reference_selection": {"count": 4}},
            force=True,
        )
        fake = dict(self.project)
        fake["scenes"] = [{
            "id": "SCENE_E",
            "scene_index": 0,
            "flow_prompt": "BASE PROMPT",
            "props_present": ["PROP_002"],
        }]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.start_pipeline", return_value={"run": {"id": "run-qc-repair"}}
        ):
            retry_scene(self.pid, "SCENE_E")
        state = get_scene_state(self.pid, "SCENE_E")
        snap = state.get("snapshot") or {}
        prompt = snap.get("operator_repair_prompt") or ""
        self.assertEqual(state["attempt"], 5)
        self.assertIn("REPAIR CONSTRAINTS", prompt)
        self.assertIn("PROP_002", prompt)
        self.assertIn("hands frozen at 7:00", prompt)
        self.assertEqual((snap.get("reference_selection") or {}).get("count"), 4)

    def test_reference_snapshot_merge_preserves_operator_repair_prompt(self):
        from .film_pipeline_service import _merged_scene_snapshot
        upsert_scene_state(
            self.pid,
            "SCENE_E",
            0,
            status="QUEUED",
            attempt=5,
            snapshot={
                "operator_repair_prompt": "BASE\n\nREPAIR CONSTRAINTS\n- keep PROP_002 canonical",
                "reference_selection": {"count": 4},
            },
            force=True,
        )
        merged = _merged_scene_snapshot(
            self.pid,
            "SCENE_E",
            reference_overflow={"blocked": False},
            reference_selection={"count": 6},
        )
        self.assertIn("operator_repair_prompt", merged)
        self.assertIn("PROP_002", merged["operator_repair_prompt"])
        self.assertEqual((merged.get("reference_selection") or {}).get("count"), 6)
        self.assertFalse((merged.get("reference_overflow") or {}).get("blocked"))

    def test_start_pipeline_preserves_existing_attempt(self):
        from .film_scene_state_store import get_active_run
        with connect() as conn:
            conn.execute(
                "UPDATE film_pipeline_runs SET status='failed' WHERE project_id=? AND status IN ('running','paused','stopping')",
                (self.pid,),
            )
        upsert_scene_state(
            self.pid, "SCENE_E", 0, status="QUEUED", attempt=3,
            current_job_id=None, error=None, blocked_reason=None, force=True,
        )
        fake = dict(self.project)
        fake["scenes"] = [{"id": "SCENE_E", "scene_index": 0}]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            start_pipeline(self.pid, from_scene_id="SCENE_E")
        state = get_scene_state(self.pid, "SCENE_E")
        self.assertEqual(state["attempt"], 3)
        self.assertEqual(state["status"], "QUEUED")
        with connect() as conn:
            conn.execute(
                "UPDATE film_pipeline_runs SET status='failed' WHERE project_id=? AND status IN ('running','paused','stopping')",
                (self.pid,),
            )

    def test_start_pipeline_new_scene_starts_at_zero(self):
        with connect() as conn:
            conn.execute(
                "UPDATE film_pipeline_runs SET status='failed' WHERE project_id=? AND status IN ('running','paused','stopping')",
                (self.pid,),
            )
            conn.execute("DELETE FROM film_scene_pipeline WHERE project_id=? AND scene_id='SCENE_NEW'", (self.pid,))
        fake = dict(self.project)
        fake["scenes"] = [{"id": "SCENE_NEW", "scene_index": 9}]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            start_pipeline(self.pid, from_scene_id="SCENE_NEW")
        state = get_scene_state(self.pid, "SCENE_NEW")
        self.assertEqual(state["attempt"], 0)
        with connect() as conn:
            conn.execute("DELETE FROM film_scene_pipeline WHERE project_id=? AND scene_id='SCENE_NEW'", (self.pid,))
            conn.execute(
                "UPDATE film_pipeline_runs SET status='failed' WHERE project_id=? AND status IN ('running','paused','stopping')",
                (self.pid,),
            )
    def test_double_retry_junction_is_idempotent(self):
        upsert_junction(self.pid, "S1", "S2", status="FAIL")
        row = get_pair_junction(self.pid, "S1", "S2")
        fake = dict(self.project)
        fake["scenes"] = [{"id": "S1", "scene_index": 3}, {"id": "S2", "scene_index": 4}]
        with patch("app.film_boundary_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.start_pipeline", return_value={"run": {"id": "jrun"}}
        ):
            a = retry_junction(self.pid, row["id"])
            b = retry_junction(self.pid, row["id"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["status"], "REPAIRING")
        self.assertEqual(a.get("attempt"), b.get("attempt"))

    def test_double_final_assemble_is_idempotent(self):
        row = create_final_render(self.pid, status="QC_PENDING", manifest={"gate": {"passed": True}})
        a = assemble_project(self.pid)
        b = assemble_project(self.pid)
        self.assertEqual(a["current"]["id"], row["id"])
        self.assertEqual(b["current"]["id"], row["id"])

    def test_double_snapshot_backfill_is_idempotent(self):
        a = backfill_acceptance_snapshots(self.pid)
        b = backfill_acceptance_snapshots(self.pid)
        self.assertEqual(b["created_count"], 0)
        self.assertEqual(a["project_id"], b["project_id"])


class CapabilityTests(Batch4Case):
    def test_capability_matrix_refresh(self):
        rows = ingest_capability_payload("video", {
            "models": ["__B4_VEO__", "Nano Banana 2 Lite crop_16_9 x1", "__B4_VEO__ [Lower Priority]"],
            "resolutions": ["720p"],
            "durations": [4, 6, 8],
            "aspect_ratios": ["16:9"],
            "max_references": 4,
        })
        names = {item["model"] for item in rows}
        self.assertIn("__B4_VEO__", names)
        self.assertEqual([item["model"] for item in rows], ["__B4_VEO__"])
        self.assertTrue(all("crop" not in name.lower() for name in names))
        self.assertIsNone(canonical_model_name("Nano Banana 2 Lite crop_16_9 x1"))

    def test_reference_limit_enforced(self):
        upsert_capability({
            "provider": "flow", "model": "__B4_REF__", "media_type": "video",
            "max_references": 3, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        report = evaluate_capability(model="__B4_REF__", reference_count=5, duration=8, resolution="720p", aspect_ratio="16:9")
        self.assertTrue(report.get("blocked"))
        self.assertEqual(report.get("code"), "CAPABILITY_REF_LIMIT")

    def test_unsupported_duration_blocks(self):
        upsert_capability({
            "provider": "flow", "model": "__B4_DUR__", "media_type": "video",
            "max_references": 4, "resolutions": ["720p"], "durations": [4, 6, 8], "aspect_ratios": ["16:9"],
        })
        report = evaluate_capability(model="__B4_DUR__", duration=10, resolution="720p")
        self.assertTrue(report.get("blocked"))
        self.assertEqual(report.get("code"), "CAPABILITY_DURATION_UNSUPPORTED")

    def test_unsupported_resolution_blocks(self):
        upsert_capability({
            "provider": "flow", "model": "__B4_RES__", "media_type": "video",
            "max_references": 4, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        report = evaluate_capability(model="__B4_RES__", duration=8, resolution="1080p")
        self.assertTrue(report.get("blocked"))
        self.assertEqual(report.get("code"), "CAPABILITY_RESOLUTION_UNSUPPORTED")

    def test_model_selection_respects_capability(self):
        upsert_capability({
            "provider": "flow", "model": "__B4_SMALL__", "media_type": "video",
            "max_references": 2, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        upsert_capability({
            "provider": "flow", "model": "__B4_BIG__", "media_type": "video",
            "max_references": 4, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        report = select_model(reference_count=3, duration=8, resolution="720p", preferred="__B4_SMALL__", fallback_models=["__B4_BIG__"])
        self.assertTrue(report.get("ok"))
        self.assertEqual(report.get("selected"), "__B4_BIG__")
        self.assertTrue(report.get("auto"))

    def test_refresh_uses_flow_binding_and_rebinds_stale_project(self):
        project = dict(self.project)
        project["settings"] = {
            "flow_project_id": "flow-old",
            "flow_model": "__B4_REFRESH_VIDEO__",
        }
        calls = []

        async def fake_video(flow_project_id):
            calls.append(("video", flow_project_id))
            if flow_project_id == "flow-old":
                raise RuntimeError("PROJECT_NOT_FOUND")
            return {
                "project_id": "flow-new",
                "models": ["__B4_REFRESH_VIDEO__"],
                "resolutions": ["720p"],
                "durations": [8],
                "aspect_ratios": ["16:9"],
                "max_references": 4,
            }

        async def fake_image(flow_project_id):
            calls.append(("image", flow_project_id))
            return {
                "project_id": "flow-new",
                "models": ["__B4_REFRESH_IMAGE__"],
                "aspect_ratios": ["16:9"],
            }

        async def fake_projects():
            return {"projects": [{"id": "flow-new"}], "count": 1}

        with patch("app.film_store.get_film_project", return_value=project), patch(
            "app.film_store.update_film_project"
        ) as update_project, patch(
            "app.flow_bridge_client.get_flow_video_capabilities", side_effect=fake_video
        ), patch(
            "app.flow_bridge_client.get_flow_image_capabilities", side_effect=fake_image
        ), patch(
            "app.flow_bridge_client.get_flow_projects", side_effect=fake_projects
        ):
            result = asyncio.run(refresh_capability_matrix(self.pid))

        self.assertEqual(calls[0], ("video", "flow-old"))
        self.assertIn(("video", "flow-new"), calls)
        self.assertEqual(result.get("flow_project_id"), "flow-new")
        merged = update_project.call_args.kwargs["settings"]
        self.assertEqual(merged.get("flow_project_id"), "flow-new")

    def test_refresh_rebinds_when_stored_workspace_has_empty_models(self):
        project = dict(self.project)
        preferred = "Veo 3.1 - Lite [Lower Priority]"
        project["settings"] = {
            "flow_project_id": "flow-old",
            "flow_model": preferred,
        }
        calls = []

        async def fake_video(flow_project_id):
            calls.append(("video", flow_project_id))
            if flow_project_id == "flow-old":
                return {
                    "project_id": "flow-old",
                    "models": [],
                    "resolutions": ["720p"],
                    "durations": [8],
                    "aspect_ratios": ["16:9"],
                }
            return {
                "project_id": "flow-new",
                "models": [preferred, "Veo 3.1 - Fast"],
                "resolutions": ["720p"],
                "durations": [8],
                "aspect_ratios": ["16:9"],
                "max_references": 6,
            }

        async def fake_image(flow_project_id):
            return {
                "project_id": flow_project_id,
                "models": ["Nano Banana 2"],
                "aspect_ratios": ["16:9"],
            }

        async def fake_projects():
            return {
                "projects": [{"id": "flow-new"}, {"id": "flow-old"}],
                "count": 2,
            }

        with patch("app.film_store.get_film_project", return_value=project), patch(
            "app.film_store.update_film_project"
        ) as update_project, patch(
            "app.flow_bridge_client.get_flow_video_capabilities", side_effect=fake_video
        ), patch(
            "app.flow_bridge_client.get_flow_image_capabilities", side_effect=fake_image
        ), patch(
            "app.flow_bridge_client.get_flow_projects", side_effect=fake_projects
        ):
            result = asyncio.run(refresh_capability_matrix(self.pid))

        self.assertEqual(calls[:2], [("video", "flow-old"), ("video", "flow-new")])
        self.assertEqual(result.get("flow_project_id"), "flow-new")
        self.assertTrue(result.get("video"))
        self.assertNotIn("unknown", [row.get("model") for row in result.get("video") or []])
        merged = update_project.call_args.kwargs["settings"]
        self.assertEqual(merged.get("flow_project_id"), "flow-new")

    def test_unknown_only_matrix_is_not_fresh(self):
        now = datetime.now(timezone.utc).isoformat()
        with patch(
            "app.film_capability_matrix.list_capability_matrix",
            return_value=[{"model": "unknown", "checked_at": now}],
        ):
            self.assertFalse(matrix_is_fresh("video"))

    def test_stale_capability_refresh(self):
        upsert_capability({
            "provider": "flow", "model": "__B4_STALE__", "media_type": "video",
            "max_references": 4, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        self.assertTrue(matrix_is_fresh("video"))
        old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        with connect() as conn:
            backup = [(row["id"], row["checked_at"]) for row in conn.execute("SELECT id, checked_at FROM film_capability_matrix").fetchall()]
            conn.execute("UPDATE film_capability_matrix SET checked_at=?", (old,))
        try:
            self.assertFalse(matrix_is_fresh("video"))
        finally:
            with connect() as conn:
                for item_id, stamp in backup:
                    conn.execute("UPDATE film_capability_matrix SET checked_at=? WHERE id=?", (stamp, item_id))


    def test_video_mode_selector_found(self):
        from flow_bridge.browser import FlowBrowser
        import asyncio as _asyncio

        seen = []

        class _Btn:
            def __init__(self, text):
                self._text = text
            async def inner_text(self, timeout=None):
                return self._text
            async def click(self, force=False):
                seen.append(self._text)

        class _Loc:
            def __init__(self, items):
                self._items = items
            async def count(self):
                return len(self._items)
            def nth(self, i):
                return self._items[i]
            @property
            def first(self):
                return self._items[0] if self._items else _Missing()
            def filter(self, *a, **k):
                return self

        class _Missing:
            async def count(self):
                return 0

        class _Page:
            def locator(self, sel):
                if sel == "button:visible":
                    return _Loc([_Btn("home"), _Btn("videocam Video"), _Btn("image Hình ảnh")])
                return _Loc([])
            @property
            def keyboard(self):
                class _K:
                    async def press(self, *a, **k):
                        pass
                return _K()
            async def wait_for_timeout(self, ms):
                pass

        async def _run():
            b = FlowBrowser()
            # stub settings open to no-op
            async def _noop(page):
                return None
            b._ensure_settings_open = _noop
            await b._switch_generation_mode(_Page(), "Video")
            return True

        self.assertTrue(asyncio.run(_run()))
        self.assertTrue(any("Video" in t for t in seen))

    def test_video_model_selector_fallback(self):
        import asyncio as _asyncio
        from flow_bridge.browser import FlowBrowser

        class _Btn:
            def __init__(self, text="", aria=""):
                self._text = text
                self._aria = aria
                self.clicked = False
            async def inner_text(self, timeout=None):
                return self._text
            async def get_attribute(self, name):
                return self._aria if name == "aria-label" else None
            async def click(self, force=False):
                self.clicked = True

        class _Loc:
            def __init__(self, items):
                self._items = items
            async def count(self):
                return len(self._items)
            def nth(self, i):
                return self._items[i]
            @property
            def first(self):
                return self._items[0]

        class _Page:
            def locator(self, sel):
                # canonical aria selectors miss, text fallback must hit
                if "aria-label" in sel:
                    return _Loc([])
                if sel == "button:visible":
                    return _Loc([_Btn("Veo 3.1 - Lite [Lower Priority] arrow_drop_down", "")])
                return _Loc([])

        async def _run():
            b = FlowBrowser()
            found, strategy = await b._find_model_button(_Page())
            return found is not None, strategy

        found, strategy = asyncio.run(_run())
        self.assertTrue(found)
        self.assertEqual(strategy, "text:model-family")

    def test_video_model_canonical_label_match(self):
        from flow_bridge.browser import canonical_video_model_variants
        variants = canonical_video_model_variants("Veo 3.1 - Lite [Lower Priority]")
        self.assertIn("Veo 3.1 - Lite [Lower Priority]", variants)
        self.assertIn("Veo 3.1 - Lite", variants)
        # capability canonical strips the suffix
        self.assertEqual(canonical_model_name("Veo 3.1 - Lite [Lower Priority]"), "Veo 3.1 - Lite")
        # NOTE: use a __B4__-namespaced model so cleanup never deletes
        # production capability rows (e.g. real "Veo 3.1 - Lite").
        self.assertEqual(canonical_model_name("__B4_VEO_LITE__ [Lower Priority]"), "__B4_VEO_LITE__")
        upsert_capability({
            "provider": "flow", "model": "__B4_VEO_LITE__", "media_type": "video",
            "max_references": 4, "resolutions": ["720p"], "durations": [8], "aspect_ratios": ["16:9"],
        })
        try:
            report = evaluate_capability(model="__B4_VEO_LITE__ [Lower Priority]", duration=8, resolution="720p")
            # canonical match must resolve the Lower Priority suffix to the base row
            self.assertTrue(report.get("ok"))
            self.assertEqual(report.get("model"), "__B4_VEO_LITE__")
        finally:
            with connect() as conn:
                conn.execute("DELETE FROM film_capability_matrix WHERE model='__B4_VEO_LITE__'")

    def test_missing_selector_returns_flow_ui_changed(self):
        import sys
        sys.path.insert(0, "backend")
        from flow_bridge.app import _classify_error
        code = _classify_error(RuntimeError("Không tìm thấy selector model để chuyển sang Video."))
        self.assertEqual(code, "FLOW_UI_CHANGED")

    def test_low_priority_model_never_falls_back_to_paid_variant(self):
        from flow_bridge.browser import model_selection_variants
        # Bản [Lower Priority] (không tốn credits): strict exact, không fallback bản base trả phí
        self.assertEqual(
            model_selection_variants("Veo 3.1 - Lite [Lower Priority]"),
            ["Veo 3.1 - Lite [Lower Priority]"],
        )
        # Bản thường vẫn có canonical fallback
        self.assertEqual(
            model_selection_variants("Veo 3.1 - Fast"),
            ["Veo 3.1 - Fast"],
        )
        self.assertEqual(model_selection_variants(None), [])
        self.assertEqual(model_selection_variants("  "), [])


if __name__ == "__main__":
    unittest.main()
