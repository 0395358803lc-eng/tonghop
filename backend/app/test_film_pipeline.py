import unittest
from unittest.mock import patch

from . import film_production_gate_v2 as gate_v2
from .film_pipeline_service import (
    _flow_provider_safety_prompt,
    _mark_flow_dependency_blocked,
    _prepare_scene_status,
    build_flow_reference_payload,
    enforce_reference_capacity,
    pause_pipeline,
    pipeline_status,
    pipeline_worker_active,
    process_pipeline,
    resolve_reference_priority,
    resume_pipeline,
    retry_scene,
    start_pipeline,
    stop_pipeline,
)
from .film_flow_errors import is_terminal_render_error
from .film_repair_service import build_repair_prompt, is_better_candidate, rank_tuple
from .film_render_store import create_render_jobs, get_render_job, list_render_jobs, refresh_render_job_context, update_render_job
from .film_scene_qc_v2 import evaluate_qc_v2
from .film_scene_state_store import (
    can_transition,
    ensure_scene_states,
    get_ledger,
    get_scene_state,
    list_candidates,
    record_candidate,
    save_ledger,
    select_best_candidate,
    transition_status,
    upsert_scene_state,
)
from .db import init_db
from .film_store import append_film_scenes, create_film_project, delete_film_project


class SceneStatusMachineTests(unittest.TestCase):
    def test_happy_path(self):
        path = ["LOCKED", "WAITING_REFERENCE", "QUEUED", "GENERATING", "QC_RUNNING", "APPROVED"]
        current = path[0]
        for nxt in path[1:]:
            current = transition_status(current, nxt)
        self.assertEqual(current, "APPROVED")

    def test_fail_retry_path(self):
        current = transition_status("QC_RUNNING", "QC_FAILED")
        current = transition_status(current, "REGENERATING")
        current = transition_status(current, "QC_RUNNING")
        self.assertEqual(current, "QC_RUNNING")

    def test_denied_skip_to_approved(self):
        with self.assertRaises(ValueError):
            transition_status("GENERATING", "APPROVED")
        with self.assertRaises(ValueError):
            transition_status("LOCKED", "APPROVED")
        self.assertFalse(can_transition("APPROVED", "QUEUED"))

    def test_dependency_and_stale(self):
        self.assertEqual(transition_status("QUEUED", "BLOCKED"), "BLOCKED")
        self.assertEqual(transition_status("APPROVED", "STALE"), "STALE")
        self.assertEqual(transition_status("STALE", "WAITING_REFERENCE"), "WAITING_REFERENCE")





class FlowProviderSafetyTests(unittest.TestCase):
    def test_fictional_context_is_added_once_without_changing_story_text(self):
        base = "[UNIT]\nSCENE_X\n\n[SOURCE_ACTION_IMMUTABLE]\nOriginal story action."
        wrapped = _flow_provider_safety_prompt(base)
        self.assertTrue(wrapped.startswith("[FICTIONAL_CHARACTER_CONTEXT]"))
        self.assertIn("original fictional characters", wrapped)
        self.assertIn(base, wrapped)
        self.assertEqual(_flow_provider_safety_prompt(wrapped), wrapped)

    def test_policy_block_is_terminal_render_error(self):
        self.assertTrue(
            is_terminal_render_error(
                "FLOW_POLICY_BLOCKED: request may violate public figure policy"
            )
        )
        self.assertFalse(
            is_terminal_render_error("GENERATION_TIMEOUT: provider timed out")
        )

class QcV2HardGateTests(unittest.TestCase):
    def test_identity_fail_even_if_overall_high(self):
        scene = {"characters": ["CHAR_001"], "location_id": "LOC_001"}
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 96,
                "qc": {
                    "passed": True,
                    "consistency_score": 96,
                    "dimension_scores": {
                        "identity": 40,
                        "wardrobe": 92,
                        "location": 90,
                        "prop": 90,
                        "boundary": 90,
                    },
                },
            },
            scene,
            {"reference": {}},
        )
        self.assertEqual(result["qc_status"], "failed")
        self.assertFalse(result["qc"]["passed"])
        self.assertEqual(result["consistency_score"], 96)
        self.assertTrue(result["qc"]["identity_independent_fail"])
        self.assertIn("identity", result["qc"]["hard_gate"]["failed"])

    def test_identity_pass_keeps_pass(self):
        scene = {"characters": ["CHAR_001"]}
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 91,
                "qc": {"passed": True, "consistency_score": 91, "dimension_scores": {"identity": 94, "wardrobe": 90, "camera": 88, "lighting": 86}},
            },
            scene,
            {"reference": {}},
        )
        self.assertEqual(result["qc_status"], "passed")
        self.assertTrue(result["qc"]["hard_gate"]["passed"])


class RepairAndBestCandidateTests(unittest.TestCase):
    def test_repair_mentions_identity(self):
        scene = {"characters": ["CHAR_001"], "flow_prompt": "A quiet hallway."}
        qc = {"qc": {"hard_gate": {"failed": ["identity"]}, "dimensions": {"identity": {"passed": False}}, "issues": []}}
        repair = build_repair_prompt(scene, qc, scene["flow_prompt"])
        self.assertIn("identity", repair["failed_dimensions"])
        self.assertIn("CHAR_001", repair["prompt"])
        self.assertIn("REPAIR CONSTRAINTS", repair["prompt"])

    def test_repair_state_override_precedes_generic_habit_and_bans_unlisted_prop(self):
        scene = {
            "characters": ["CHAR_001"],
            "props_present": [],
            "start_state": "CHAR_001 exiting; no PROP_001 because the box remains inside.",
            "end_state": "CHAR_001 walking with empty hands.",
            "flow_prompt": "[CHARACTER_CANON]\nsignature_traits: nắm chặt hộp nhỏ khi đứng chờ",
        }
        qc = {
            "qc": {
                "hard_gate": {"failed": ["boundary"]},
                "dimensions": {"boundary": {"passed": False}},
                "issues": [
                    {
                        "type": "prop",
                        "severity": "critical",
                        "expected": "Tay nhân vật phải trống rỗng; không được cầm hộp.",
                    }
                ],
            }
        }
        repair = build_repair_prompt(scene, qc, scene["flow_prompt"])
        prompt = repair["prompt"]
        self.assertLess(prompt.index("REPAIR CONSTRAINTS"), prompt.index("[CHARACTER_CANON]"))
        self.assertIn("CURRENT START_STATE: CHAR_001 exiting; no PROP_001", prompt)
        self.assertIn("Do not invent, carry or hold any handheld prop/object", prompt)
        self.assertIn("Tay nhân vật phải trống rỗng", prompt)

    def test_best_candidate_ranking(self):
        worse = {"hard_gates_passed": False, "dimensions_passed": 8, "overall_score": 99}
        better = {"hard_gates_passed": True, "dimensions_passed": 4, "overall_score": 70}
        self.assertTrue(is_better_candidate(better, worse))
        self.assertFalse(is_better_candidate(worse, better))
        self.assertGreater(rank_tuple(True, 2, 10), rank_tuple(False, 9, 99))


class ReferenceResolverTests(unittest.TestCase):
    def test_does_not_silently_drop_character(self):
        manifest = {
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "location", "entity_id": "LOC_001", "local_path": "b.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_001", "local_path": "c.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_002", "local_path": "d.jpg"},
            ]
        }
        resolved = resolve_reference_priority(manifest, "prev.jpg", max_references=2)
        buckets = [x["bucket"] for x in resolved["selected"]]
        self.assertEqual(buckets[0], "character")
        self.assertEqual(buckets[1], "previous_last_frame")
        self.assertTrue(resolved["overflow"]["blocked"])


class PipelineStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.project = create_film_project(
            "__pipeline_core_test__",
            "Day la kich ban kiem thu pipeline dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )

    @classmethod
    def tearDownClass(cls):
        delete_film_project(cls.project["id"])

    def test_ledger_only_via_save(self):
        pid = self.project["id"]
        ensure_scene_states(pid, [{"id": "SCENE_A", "scene_index": 0}, {"id": "SCENE_B", "scene_index": 1}])
        upsert_scene_state(pid, "SCENE_A", 0, status="QUEUED")
        upsert_scene_state(pid, "SCENE_A", 0, status="GENERATING")
        self.assertIsNone(get_ledger(pid, "SCENE_A"))
        upsert_scene_state(pid, "SCENE_A", 0, status="QC_RUNNING")
        upsert_scene_state(pid, "SCENE_A", 0, status="QC_FAILED")
        self.assertIsNone(get_ledger(pid, "SCENE_A"))
        save_ledger(pid, "SCENE_A", {
            "selected_media_id": "media-1",
            "accepted_last_frame": "/api/flow/render/abc/last-frame",
            "location_id": "LOC_001",
        })
        upsert_scene_state(pid, "SCENE_A", 0, status="APPROVED", selected_media_id="media-1")
        ledger = get_ledger(pid, "SCENE_A")
        self.assertEqual(ledger["selected_media_id"], "media-1")
        self.assertEqual(ledger["accepted_last_frame"], "/api/flow/render/abc/last-frame")
        self.assertEqual(get_scene_state(pid, "SCENE_A")["status"], "APPROVED")

    def test_sequential_block_without_previous_approved(self):
        pid = self.project["id"]
        ensure_scene_states(pid, [{"id": "SCENE_X", "scene_index": 10}, {"id": "SCENE_Y", "scene_index": 11}])
        from .film_pipeline_service import _previous_last_frame
        prev = _previous_last_frame(pid, 11)
        self.assertTrue(prev["required"])
        self.assertFalse(prev["approved"])
        self.assertIsNone(prev["url"])

    def test_prepare_stale_scene_does_not_reapprove_old_selected_media(self):
        scene = {"id": "SCENE_STALE", "scene_index": 9}
        existing_media = {
            "id": "old-media",
            "status": "completed",
            "qc_status": "passed",
            "provider_job_id": "old-job",
        }
        with patch(
            "app.film_pipeline_service.get_scene_state",
            return_value={"status": "STALE", "attempt": 3},
        ), patch(
            "app.film_pipeline_service.get_selected_media",
            return_value=existing_media,
        ), patch(
            "app.film_pipeline_service.has_previous_scene",
            return_value=False,
        ), patch(
            "app.film_pipeline_service.upsert_scene_state",
        ) as upsert:
            _prepare_scene_status("P1", scene, "run-1", "SCENE_STALE")

        statuses = [call.kwargs.get("status") for call in upsert.call_args_list]
        self.assertNotIn("APPROVED", statuses)
        self.assertEqual(statuses[-1], "QUEUED")
        self.assertEqual(upsert.call_args_list[-1].kwargs.get("attempt"), 4)

    def test_prepare_operator_retry_does_not_reapprove_old_selected_media(self):
        scene = {"id": "SCENE_RETRY", "scene_index": 10}
        existing_media = {
            "id": "old-media",
            "status": "completed",
            "qc_status": "passed",
            "provider_job_id": "old-job",
        }
        state = {
            "status": "QUEUED",
            "attempt": 4,
            "snapshot": {"operator_force_generation": True},
        }
        with patch(
            "app.film_pipeline_service.get_scene_state",
            return_value=state,
        ), patch(
            "app.film_pipeline_service.get_selected_media",
            return_value=existing_media,
        ), patch(
            "app.film_pipeline_service.has_previous_scene",
            return_value=False,
        ), patch(
            "app.film_pipeline_service.upsert_scene_state",
        ) as upsert:
            _prepare_scene_status("P1", scene, "run-2", "SCENE_RETRY")

        statuses = [call.kwargs.get("status") for call in upsert.call_args_list]
        self.assertNotIn("APPROVED", statuses)
        self.assertEqual(statuses[-1], "QUEUED")
        self.assertEqual(upsert.call_args_list[-1].kwargs.get("attempt"), 4)

    def test_best_candidate_persisted(self):
        pid = self.project["id"]
        record_candidate(pid, "SCENE_A", run_id=None, job_id="j1", media_id="m1", attempt=0, hard_gates_passed=False, dimensions_passed=8, overall_score=99, qc={})
        record_candidate(pid, "SCENE_A", run_id=None, job_id="j2", media_id="m2", attempt=1, hard_gates_passed=True, dimensions_passed=3, overall_score=71, qc={})
        best = select_best_candidate(pid, "SCENE_A")
        self.assertEqual(best["media_id"], "m2")
        self.assertTrue(best["is_best"])
        self.assertEqual(sum(1 for x in list_candidates(pid, "SCENE_A") if x.get("is_best")), 1)

    def test_refresh_render_job_context_preserves_repair_prompt(self):
        project = create_film_project(
            "__render_prompt_refresh__",
            "Day la kich ban kiem thu refresh render prompt dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [{
                "id": "SCENE_PROMPT_REFRESH",
                "scene_index": 0,
                "flow_prompt": "BASE SCENE PROMPT",
                "visual_prompt": "BASE VISUAL PROMPT",
                "start_state": "start",
                "end_state": "end",
            }], start_index=0)
            jobs = create_render_jobs(pid, ["SCENE_PROMPT_REFRESH"], "flow", attempt=4)
            self.assertEqual(len(jobs), 1)
            job_id = jobs[0]["id"]
            repair_prompt = "REPAIR CONSTRAINTS — HIGHEST PRIORITY\n- no box\nORIGINAL LOCKED SCENE PROMPT\nBASE"
            update_render_job(job_id, prompt=repair_prompt)
            refreshed = refresh_render_job_context(job_id)
            self.assertEqual(refreshed["prompt"], repair_prompt)
            self.assertEqual(get_render_job(job_id)["prompt"], repair_prompt)
        finally:
            delete_film_project(pid)

    def test_no_queue_all_helper_creates_one_scene_contract(self):
        from . import film_pipeline_service as svc
        with open(svc.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("pipeline chỉ được queue đúng 1 scene", src)
        self.assertIn("create_render_jobs(project_id, [scene_id]", src)
        self.assertNotIn("enqueue_render(", src)


class GateV2ErrorShapeTests(unittest.TestCase):
    def test_structured_errors(self):
        project = {
            "id": "p1",
            "status": "draft",
            "scenes": [{"id": "SCENE_004", "scene_index": 4, "continuity": {"previous_scene": "SCENE_001"}}],
            "characters": [{"id": "CHAR_001", "canonical_locked": False}],
            "locations": [],
            "props": [{"id": "PROP_002"}],
            "story_bible": {},
        }
        v1 = {"final_gate": False, "errors": [{"scene": "SCENE_004", "code": "PREVIOUS_SCENE_LINK", "detail": "broken"}]}
        fake_flow = {"configured": False, "authenticated": False, "video_available": False, "max_references": None, "error": "FLOW: video capability unavailable"}
        with patch.object(gate_v2, "_probe_flow", return_value=fake_flow), patch.object(
            gate_v2, "get_qc_status", return_value={"configured": False, "id": "none"}
        ), patch.object(gate_v2, "list_project_resources", return_value=[]), patch.object(
            gate_v2, "scene_resource_manifest", return_value={"ready": False, "missing": ["PROP_002"]}
        ), patch.object(gate_v2, "get_selected_media", return_value=None), patch.object(
            gate_v2, "list_scene_states", return_value=[]
        ):
            report = gate_v2.evaluate_production_gate_v2(project, v1)
        self.assertFalse(report["final_gate"])
        blob = " | ".join(f"{x['scene']}: {x['detail']}" for x in report["errors"])
        self.assertIn("FLOW: video capability unavailable", blob)
        self.assertTrue(any("PROP_002" in x["detail"] or "PROP_002" in x["scene"] for x in report["errors"]))
        self.assertTrue(any("CHAR_001" in x["scene"] or "CHAR_001" in x["detail"] for x in report["errors"]))


class HardenQcMissingScoreTests(unittest.TestCase):
    def test_missing_soft_dimension_is_not_auto_pass(self):
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 92,
                "qc": {"passed": True, "consistency_score": 92, "dimension_scores": {"identity": 95, "wardrobe": 90}},
            },
            {"characters": ["CHAR_001"]},
            {"reference": {}},
        )
        camera = result["qc"]["dimensions"]["camera"]
        lighting = result["qc"]["dimensions"]["lighting"]
        self.assertEqual(camera["status"], "not_evaluated")
        self.assertIsNone(camera["passed"])
        self.assertEqual(lighting["status"], "not_evaluated")
        self.assertIsNone(lighting["passed"])
        self.assertFalse(result["qc"]["qc_complete"])
        self.assertEqual(result["qc_status"], "passed")
        self.assertTrue(result["qc"]["passed"])
        self.assertTrue(result["qc"]["hard_gate"]["passed"])

    def test_required_missing_dimension_blocks_qc(self):
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 93,
                "qc": {"passed": True, "consistency_score": 93, "dimension_scores": {"wardrobe": 91, "camera": 80, "lighting": 80}},
            },
            {"characters": ["CHAR_001"]},
            {"reference": {}},
        )
        self.assertEqual(result["qc_status"], "failed")
        self.assertFalse(result["qc"]["passed"])
        self.assertIn("identity", result["qc"]["hard_gate"]["failed"])
        self.assertIn("identity", result["qc"]["incomplete_dimensions"])

class HardenReferencePayloadTests(unittest.TestCase):
    def test_reference_resolver_selected_references_reach_payload(self):
        manifest = {
            "ready": True,
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "location", "entity_id": "LOC_001", "local_path": "b.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_001", "local_path": "c.jpg"},
            ],
        }
        built = build_flow_reference_payload(manifest, "prev.jpg", 2)
        refs = built["resource_manifest"]["references"]
        kinds = [x.get("resource_type") for x in refs]
        self.assertEqual(kinds, ["character"])
        self.assertNotIn("previous_last_frame", kinds)
        self.assertEqual(built["resource_manifest"]["selection"]["count"], 2)
        self.assertIn("previous_last_frame", built["resource_manifest"]["selection"]["buckets"])
        self.assertEqual(built["reference_image_url"], "prev.jpg")
        self.assertEqual((built.get("boundary_reference") or {}).get("url"), "prev.jpg")
        self.assertNotIn("location", kinds)
        self.assertTrue((built["resolved"].get("overflow") or {}).get("blocked"))


class PipelineControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.project = create_film_project(
            "__pipeline_ctrl_test__",
            "Day la kich ban kiem thu pipeline control dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )

    @classmethod
    def tearDownClass(cls):
        delete_film_project(cls.project["id"])

    def test_pause_resume_stop_contract(self):
        from .film_scene_state_store import create_run, update_run
        pid = self.project["id"]
        from . import film_pipeline_service as svc
        with open(svc.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertLess(src.find('if run.get("status") == "paused"'), src.find("scene = _next_scene"))
        run = create_run(pid, gate={"final_gate": True})
        paused = pause_pipeline(pid)
        self.assertEqual(paused["run"]["status"], "paused")
        resumed = resume_pipeline(pid)
        self.assertEqual(resumed["run"]["status"], "running")
        stopped = stop_pipeline(pid)
        self.assertIn(stopped["run"]["status"], {"stopping", "stopped"})
        self.assertTrue(stopped["run"].get("stop_after_current") or stopped["run"]["status"] == "stopped")
        update_run(run["id"], status="stopped")

    def test_continue_from_scene_dependency_guard(self):
        pid = self.project["id"]
        scenes = [{"id": "SCENE_A", "scene_index": 0}, {"id": "SCENE_B", "scene_index": 1}]
        ensure_scene_states(pid, scenes)
        fake = dict(self.project)
        fake["scenes"] = scenes
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            with self.assertRaises(ValueError) as ctx:
                start_pipeline(pid, from_scene_id="SCENE_B")
        self.assertIn("previous scene not approved", str(ctx.exception))

    def test_retry_blocked_scene_versioning(self):
        pid = self.project["id"]
        scene = {"id": "SCENE_R", "scene_index": 0}
        fake = dict(self.project)
        fake["scenes"] = [scene]
        ensure_scene_states(pid, [scene])
        upsert_scene_state(pid, "SCENE_R", 0, status="QUEUED")
        upsert_scene_state(pid, "SCENE_R", 0, status="GENERATING")
        upsert_scene_state(pid, "SCENE_R", 0, status="QC_RUNNING")
        upsert_scene_state(pid, "SCENE_R", 0, status="QC_FAILED", error="QC V2 failed score=40 hard=identity")
        record_candidate(pid, "SCENE_R", run_id=None, job_id="old", media_id="m-old", attempt=0, hard_gates_passed=False, dimensions_passed=1, overall_score=40, qc={})
        with patch("app.film_pipeline_service.get_film_project", return_value=fake), patch(
            "app.film_pipeline_service.evaluate_production_gate_v2", return_value={"final_gate": True, "errors": []}
        ):
            retry_scene(pid, "SCENE_R")
        self.assertEqual(get_scene_state(pid, "SCENE_R")["status"], "REGENERATING")
        self.assertGreaterEqual(len(list_candidates(pid, "SCENE_R")), 1)
        with patch("app.film_pipeline_service.get_film_project", return_value=fake):
            upsert_scene_state(pid, "SCENE_R", 0, status="BLOCKED", blocked_reason="RESOURCE_LOCK_INCOMPLETE: CHAR_001", error="RESOURCE_LOCK_INCOMPLETE: CHAR_001", force=True)
            with self.assertRaises(ValueError):
                retry_scene(pid, "SCENE_R")


    def test_retry_terminal_block_forces_new_generation(self):
        pid = self.project["id"]
        scene = {"id": "SCENE_FORCE", "scene_index": 60}
        fake = dict(self.project)
        fake["scenes"] = [scene]
        append_film_scenes(pid, [scene], start_index=60)
        ensure_scene_states(pid, [scene])
        upsert_scene_state(
            pid,
            scene["id"],
            60,
            status="BLOCKED",
            attempt=2,
            current_job_id="old-terminal-job",
            blocked_reason="FLOW_RUNTIME_ERROR: prior render failed",
            error="FLOW_RUNTIME_ERROR: prior render failed",
            force=True,
        )
        with patch(
            "app.film_pipeline_service.get_film_project",
            return_value=fake,
        ), patch(
            "app.film_pipeline_service.get_active_run",
            return_value=None,
        ), patch(
            "app.film_pipeline_service.start_pipeline",
            return_value={"project_id": pid},
        ):
            retry_scene(pid, scene["id"])

        state = get_scene_state(pid, scene["id"]) or {}
        self.assertEqual(state["status"], "QUEUED")
        self.assertEqual(state["attempt"], 3)
        self.assertIsNone(state["current_job_id"])
        self.assertTrue((state.get("snapshot") or {}).get("operator_force_generation"))
        self.assertEqual((state.get("snapshot") or {}).get("operator_retry_source_attempt"), 2)

    def test_create_render_job_uses_pipeline_attempt(self):
        pid = self.project["id"]
        scene = {"id": "SCENE_ATTEMPT", "scene_index": 0, "flow_prompt": "attempt"}
        fake = dict(self.project)
        fake["scenes"] = [scene]
        append_film_scenes(pid, [scene], start_index=0)
        jobs = create_render_jobs(pid, ["SCENE_ATTEMPT"], "flow", attempt=3)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["attempt"], 3)

    def test_session_expired_blocks_without_new_attempt_or_job(self):
        pid = self.project["id"]
        scene = {
            "id": "SCENE_DEP_BLOCK",
            "scene_index": 50,
            "flow_prompt": "dependency",
            "visual_prompt": "dependency",
        }
        append_film_scenes(pid, [scene], start_index=50)
        ensure_scene_states(pid, [scene])
        upsert_scene_state(pid, scene["id"], 50, status="QUEUED", attempt=2)
        upsert_scene_state(pid, scene["id"], 50, status="GENERATING", attempt=2)
        jobs = create_render_jobs(pid, [scene["id"]], "flow", attempt=2)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        update_render_job(job["id"], status="generating", progress=35)
        upsert_scene_state(pid, scene["id"], 50, current_job_id=job["id"], attempt=2)

        before = len([x for x in list_render_jobs(pid) if x.get("scene_id") == scene["id"]])
        code = _mark_flow_dependency_blocked(
            pid,
            scene["id"],
            50,
            2,
            job,
            "SESSION_EXPIRED: Flow session hết hạn trong lúc đang chờ generation.",
        )
        after = len([x for x in list_render_jobs(pid) if x.get("scene_id") == scene["id"]])
        state = get_scene_state(pid, scene["id"])
        stored = get_render_job(job["id"])

        self.assertEqual(code, "SESSION_EXPIRED")
        self.assertEqual(before, after)
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(state["current_job_id"], job["id"])
        self.assertEqual(stored["status"], "generating")
        self.assertEqual(stored["provider_error_code"], "SESSION_EXPIRED")

        resumed_jobs = create_render_jobs(pid, [scene["id"]], "flow", attempt=2)
        self.assertEqual(len(resumed_jobs), 1)
        self.assertEqual(resumed_jobs[0]["id"], job["id"])
        self.assertEqual(
            len([x for x in list_render_jobs(pid) if x.get("scene_id") == scene["id"]]),
            before,
        )

        fake = dict(self.project)
        fake["scenes"] = [scene]
        with patch("app.film_pipeline_service.get_film_project", return_value=fake):
            with self.assertRaises(ValueError):
                retry_scene(pid, scene["id"])


class ReferenceCapacityPolicyTests(unittest.TestCase):
    def test_critical_prop_drop_blocks_pipeline(self):
        manifest = {
            "ready": True,
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_001", "local_path": "c.jpg", "critical": True},
            ],
        }
        built = build_flow_reference_payload(manifest, "prev.jpg", 2)
        with self.assertRaises(RuntimeError) as ctx:
            enforce_reference_capacity(built["resolved"])
        self.assertIn("REFERENCE_CAPACITY_EXCEEDED", str(ctx.exception))
        self.assertTrue((built["resolved"].get("overflow") or {}).get("blocked"))

    def test_required_location_drop_blocks_pipeline(self):
        manifest = {
            "ready": True,
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "location", "entity_id": "LOC_001", "local_path": "b.jpg", "required": True, "critical": True},
            ],
        }
        built = build_flow_reference_payload(manifest, "prev.jpg", 2)
        with self.assertRaises(RuntimeError):
            enforce_reference_capacity(built["resolved"])

    def test_optional_secondary_reference_can_drop(self):
        manifest = {
            "ready": True,
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_002", "local_path": "d.jpg", "optional": True, "required": False, "critical": False},
            ],
        }
        built = build_flow_reference_payload(manifest, "prev.jpg", 2)
        enforce_reference_capacity(built["resolved"])
        kinds = [x.get("resource_type") for x in built["resource_manifest"]["references"]]
        self.assertEqual(kinds, ["character"])
        self.assertEqual(built["reference_selection"]["count"], 2)
        self.assertEqual(built["reference_image_url"], "prev.jpg")
        self.assertFalse((built["resolved"].get("overflow") or {}).get("blocked"))

    def test_reference_count_never_exceeds_capability(self):
        manifest = {
            "ready": True,
            "references": [
                {"resource_type": "character", "entity_id": "CHAR_001", "local_path": "a.jpg"},
                {"resource_type": "location", "entity_id": "LOC_001", "local_path": "b.jpg"},
                {"resource_type": "prop", "entity_id": "PROP_001", "local_path": "c.jpg", "critical": True},
            ],
        }
        built = build_flow_reference_payload(manifest, "prev.jpg", 2)
        self.assertLessEqual(len(built["resource_manifest"]["references"]), 2)
        self.assertLessEqual(built["reference_selection"]["count"], 2)
        self.assertNotIn("previous_last_frame", [x.get("resource_type") for x in built["resource_manifest"]["references"]])


class PauseResumeLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_resume_does_not_create_duplicate_job(self):
        import asyncio
        from .film_scene_state_store import create_run, update_run
        from . import film_pipeline_service as svc
        init_db()
        project = create_film_project(
            "__lease_race__",
            "Day la kich ban kiem thu worker lease dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        scene = {"id": "SCENE_X", "scene_index": 1}
        ensure_scene_states(pid, [scene])
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fake_one(project_id, item, run):
            calls.append(item.get("id"))
            started.set()
            await release.wait()
            upsert_scene_state(project_id, item["id"], int(item.get("scene_index") or 1), status="APPROVED", force=True)
            return {"status": "APPROVED"}

        fake = dict(project)
        fake["scenes"] = [scene]
        create_run(pid, gate={"final_gate": True})
        try:
            with patch.object(svc, "process_one_scene", fake_one), patch.object(svc, "get_film_project", return_value=fake), patch.object(
                svc, "get_qc_status", return_value={"configured": True}
            ):
                task_a = asyncio.create_task(process_pipeline(pid))
                await asyncio.wait_for(started.wait(), 3)
                paused = pause_pipeline(pid)
                self.assertEqual(paused["run"]["status"], "paused")
                resume_pipeline(pid)
                task_b = asyncio.create_task(process_pipeline(pid))
                await asyncio.sleep(0.05)
                self.assertEqual(len(calls), 1)
                self.assertTrue(pipeline_worker_active(pid))
                release.set()
                await task_a
                await task_b
                self.assertEqual(len(calls), 1)
        finally:
            svc._PIPELINE_OWNERS.pop(pid, None)
            delete_film_project(pid)

    def test_resume_reuses_inflight_job(self):
        from .db import connect
        from .film_render_store import create_render_jobs, get_active_scene_job, update_render_job
        init_db()
        project = create_film_project(
            "__reuse_job__",
            "Day la kich ban kiem thu reuse job dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            with connect() as conn:
                conn.execute(
                    "INSERT INTO film_scenes(id,project_id,scene_index,title,duration) VALUES(?,?,?,?,?)",
                    ("SCENE_J", pid, 1, "J", 8),
                )
            first = create_render_jobs(pid, ["SCENE_J"], "flow")
            self.assertEqual(len(first), 1)
            update_render_job(first[0]["id"], status="generating", progress=40)
            second = create_render_jobs(pid, ["SCENE_J"], "flow")
            self.assertEqual(len(second), 1)
            self.assertEqual(first[0]["id"], second[0]["id"])
            active = get_active_scene_job(pid, "SCENE_J")
            self.assertEqual(active["id"], first[0]["id"])
        finally:
            delete_film_project(pid)


class LiveGateSnapshotTests(unittest.TestCase):
    def test_live_gate_does_not_use_stale_run_snapshot(self):
        from .film_scene_state_store import create_run, update_run
        init_db()
        project = create_film_project(
            "__live_gate__",
            "Day la kich ban kiem thu live gate dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            run = create_run(pid, gate={"final_gate": True, "checks": {"flow_authenticated": True, "flow_video_available": True}})
            with patch.object(gate_v2, "_probe_flow") as probe:
                status = pipeline_status(pid)
                probe.assert_not_called()
            self.assertTrue((status.get("gate") or {}).get("checks", {}).get("flow_authenticated"))
            self.assertTrue((status.get("run_gate") or {}).get("checks", {}).get("flow_authenticated"))
            live = {"final_gate": False, "checks": {"flow_authenticated": False, "flow_video_available": False}}
            self.assertNotEqual(bool(status["gate"]["checks"]["flow_authenticated"]), bool(live["checks"]["flow_authenticated"]))
            update_run(run["id"], status="stopped")
        finally:
            delete_film_project(pid)


class FlowSessionClassifyTests(unittest.TestCase):
    def test_flow_project_404_is_not_usable(self):
        from flow_bridge.browser import classify_flow_session
        result = classify_flow_session(url="https://flow.google.com/404?reason=project", workspace_ready=False)
        self.assertEqual(result["state"], "PROJECT_NOT_FOUND")
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["project_usable"])
        self.assertFalse(result["page_usable"])

    def test_flow_capabilities_require_usable_session(self):
        from flow_bridge.browser import classify_flow_session
        bad = classify_flow_session(url="https://flow.google.com/404?reason=project")
        good = classify_flow_session(url="https://flow.google.com/project/abc", workspace_ready=True)
        self.assertFalse(bad["authenticated"])
        self.assertTrue(good["authenticated"])
        self.assertEqual(good["state"], "AUTHENTICATED")
