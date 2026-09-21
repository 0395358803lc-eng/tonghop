import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from .config import DATA_DIR
from .db import init_db
from .film_boundary_qc import evaluate_junction_qc, junction_repair_target, selected_media_usable
from .film_boundary_service import check_junction, check_junction_async, recover_stale_junctions_after_snapshot_rebase, recheck_junctions_for_scene, recheck_junctions_for_scene_async, refresh_junction_staleness, retry_junction
from .film_boundary_store import get_pair_junction, upsert_junction
from .film_scene_state_store import get_scene_state, save_ledger, upsert_scene_state
from .film_store import append_film_scenes, create_film_project, delete_film_project


def _selected(scene_id, media_id):
    return {"id": media_id, "scene_id": scene_id, "is_selected": True, "status": "completed", "qc_status": "passed"}


def _jpeg(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (180, 40, 40)).save(path, "JPEG")
    return path


def _pass_obs():
    names = (
        "identity", "wardrobe", "character_position", "body_orientation",
        "prop_holder", "prop_state", "prop_owner", "location_geometry",
        "lighting", "camera_direction", "motion_direction",
        "audio_transition", "dialogue_transition",
    )
    return {name: {"score": 96, "passed": True, "evidence": "match"} for name in names}


class JunctionVisionTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.folder = DATA_DIR / "generated_media" / "_junction_tests"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.prev_frame = _jpeg(self.folder / "prev_last.jpg")
        self.next_frame = _jpeg(self.folder / "next_first.jpg")

    def _media(self, scene_id, media_id, frame: Path):
        return {
            **_selected(scene_id, media_id),
            "metadata": {"last_frame_path": str(frame), "first_frame_path": str(frame)},
        }

    def test_junction_requires_real_evidence(self):
        called = []

        def vision(_evidence):
            called.append(True)
            return _pass_obs()

        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "characters": ["CHAR_001"]},
            previous_ledger={"accepted_last_frame": "/definitely/not/a/real/frame-a.jpg"},
            next_ledger={"accepted_first_frame": "/definitely/not/a/real/frame-b.jpg"},
            previous_media=_selected("SCENE_001", "m1"),
            next_media=_selected("SCENE_002", "m2"),
            vision_fn=vision,
        )
        self.assertTrue(report["blocked"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["code"], "JUNCTION_EVIDENCE_MISSING")
        self.assertEqual(called, [])

    def test_missing_frame_blocks(self):
        called = []
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "characters": ["CHAR_001"]},
            previous_ledger={"accepted_last_frame": str(self.prev_frame)},
            next_ledger={"accepted_first_frame": "/missing/next-first.jpg"},
            previous_media=self._media("SCENE_001", "m1", self.prev_frame),
            next_media=_selected("SCENE_002", "m2"),
            vision_fn=lambda evidence: called.append(evidence) or _pass_obs(),
        )
        self.assertEqual(report["code"], "JUNCTION_EVIDENCE_MISSING")
        self.assertTrue(report["blocked"])
        self.assertFalse(report["passed"])
        self.assertEqual(called, [])

    def test_junction_vision_identity_fail(self):
        obs = _pass_obs()
        obs["identity"] = {"score": 40, "passed": False, "evidence": "different face"}
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"], "location_id": "LOC_001"},
            {"id": "SCENE_002", "characters": ["CHAR_001"], "location_id": "LOC_001"},
            previous_ledger={"accepted_last_frame": str(self.prev_frame), "character_visibility": {"CHAR_001": True}},
            next_ledger={"accepted_first_frame": str(self.next_frame)},
            previous_media=self._media("SCENE_001", "m1", self.prev_frame),
            next_media=self._media("SCENE_002", "m2", self.next_frame),
            observations=obs,
        )
        self.assertFalse(report["passed"])
        self.assertIn("identity", report["hard_gate"]["failed"])
        self.assertEqual(report["vision"], None)

    def test_junction_prop_fail(self):
        obs = _pass_obs()
        obs["prop_state"] = {"score": 30, "passed": False, "evidence": "prop missing"}
        obs["prop_holder"] = {"score": 30, "passed": False, "evidence": "wrong holder"}
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"], "props_present": ["PROP_001"], "continuity": {"critical_props": ["PROP_001"]}},
            {"id": "SCENE_002", "characters": ["CHAR_001"], "props_present": ["PROP_001"], "continuity": {"critical_props": ["PROP_001"]}},
            previous_ledger={"accepted_last_frame": str(self.prev_frame), "prop_holder": {"PROP_001": "CHAR_001"}},
            next_ledger={"accepted_first_frame": str(self.next_frame), "prop_holder": {"PROP_001": "CHAR_001"}},
            previous_media=self._media("SCENE_001", "m1", self.prev_frame),
            next_media=self._media("SCENE_002", "m2", self.next_frame),
            observations=obs,
        )
        self.assertFalse(report["passed"])
        self.assertTrue({"prop_state", "prop_holder"} & set(report["hard_gate"]["failed"]))


    def test_scripted_hard_cut_does_not_require_unrelated_visual_match(self):
        called = []
        obs = _pass_obs()
        for name in (
            "identity", "wardrobe", "character_position", "body_orientation",
            "prop_owner", "prop_holder", "prop_state", "location_geometry",
            "lighting", "motion_direction", "camera_direction",
        ):
            obs[name] = {"score": 0, "passed": False, "evidence": "expected visual cut"}

        report = evaluate_junction_qc(
            {
                "id": "SCENE_014",
                "characters": ["CHAR_001"],
                "location_id": "LOC_001",
                "props_present": [],
                "end_state_structured": {"props": {}},
            },
            {
                "id": "SCENE_015",
                "characters": ["CHAR_002"],
                "location_id": "LOC_002",
                "props_present": ["PROP_001", "PROP_002"],
                "start_state_structured": {
                    "props": {
                        "PROP_001": {"state": "open"},
                        "PROP_002": {"state": "ticking"},
                    }
                },
            },
            previous_ledger={
                "accepted_last_frame": str(self.prev_frame),
                "audio_state": '{"present": true, "non_silent": true, "mean_volume_db": -24.0}',
            },
            next_ledger={
                "accepted_first_frame": str(self.next_frame),
                "audio_state": '{"present": true, "non_silent": true, "mean_volume_db": -24.0}',
            },
            previous_media=self._media("SCENE_014", "m14", self.prev_frame),
            next_media=self._media("SCENE_015", "m15", self.next_frame),
            vision_fn=lambda evidence: called.append(evidence) or obs,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(called, [])
        self.assertEqual(report["hard_gate"]["failed"], [])
        self.assertEqual(report["evidence"]["boundary_mode"], "hard_cut")
        self.assertFalse(report["evidence"]["vision_required"])
        self.assertEqual(report["evidence"]["continuing_characters"], [])
        self.assertEqual(report["evidence"]["boundary_critical_props"], [])
        self.assertFalse(report["evidence"]["same_location"])
        self.assertEqual(report["dimensions"]["identity"]["status"], "not_required")
        self.assertEqual(report["dimensions"]["prop_holder"]["status"], "not_required")
        self.assertEqual(report["dimensions"]["prop_state"]["status"], "not_required")
        self.assertEqual(report["dimensions"]["location_geometry"]["status"], "not_required")
        self.assertEqual(report["dimensions"]["audio_transition"]["status"], "passed")

    def test_junction_position_fail(self):
        obs = _pass_obs()
        obs["character_position"] = {"score": 20, "passed": False, "evidence": "jumped sides"}
        obs["body_orientation"] = {"score": 20, "passed": False, "evidence": "turned around"}
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"], "location_id": "LOC_001"},
            {"id": "SCENE_002", "characters": ["CHAR_001"], "location_id": "LOC_001"},
            previous_ledger={"accepted_last_frame": str(self.prev_frame)},
            next_ledger={"accepted_first_frame": str(self.next_frame)},
            previous_media=self._media("SCENE_001", "m1", self.prev_frame),
            next_media=self._media("SCENE_002", "m2", self.next_frame),
            observations=obs,
        )
        self.assertFalse(report["passed"])
        self.assertTrue({"character_position", "body_orientation"} & set(report["hard_gate"]["failed"]))

    def test_junction_uses_selected_media_only(self):
        unselected = {"id": "old", "scene_id": "SCENE_001", "is_selected": False, "status": "completed", "qc_status": "passed"}
        ok, code = selected_media_usable(unselected, "SCENE_001")
        self.assertFalse(ok)
        self.assertEqual(code, "JUNCTION_SELECTED_MEDIA_REQUIRED")
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "characters": ["CHAR_001"]},
            previous_media=unselected,
            next_media=_selected("SCENE_002", "m2"),
            vision_fn=lambda evidence: _pass_obs(),
        )
        self.assertTrue(report["blocked"])
        self.assertEqual(report["code"], "JUNCTION_SELECTED_MEDIA_REQUIRED")

    def test_incomplete_hard_dimension_blocks(self):
        obs = _pass_obs()
        obs["identity"] = {"score": None, "evidence": "not visible"}
        report = evaluate_junction_qc(
            {"id": "SCENE_001", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "characters": ["CHAR_001"]},
            previous_ledger={"accepted_last_frame": str(self.prev_frame)},
            next_ledger={"accepted_first_frame": str(self.next_frame)},
            previous_media=self._media("SCENE_001", "m1", self.prev_frame),
            next_media=self._media("SCENE_002", "m2", self.next_frame),
            observations=obs,
        )
        self.assertFalse(report["passed"])
        self.assertIn("identity", report["incomplete_dimensions"])
        self.assertEqual(report["code"], "JUNCTION_VISION_INCOMPLETE")

    def test_junction_retry_targets_next_scene(self):
        target = junction_repair_target({"id": "SCENE_001"}, {"id": "SCENE_002"}, {"accepted_last_frame": str(self.prev_frame)})
        self.assertEqual(target["scene_id"], "SCENE_002")
        self.assertEqual(target["prefer"], "next")
        broken = junction_repair_target({"id": "SCENE_001"}, {"id": "SCENE_002"}, {})
        self.assertEqual(broken["prefer"], "previous")

    def test_junction_retry_runs_pipeline(self):
        project = create_film_project(
            "__junction_retry_pipe__",
            "Day la kich ban kiem thu junction retry pipeline dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", attempt=0, current_job_id="old-job", force=True)
            save_ledger(pid, "SCENE_001", {"accepted_last_frame": str(self.prev_frame), "accepted_first_frame": str(self.prev_frame)})
            row = upsert_junction(pid, "SCENE_001", "SCENE_002", status="FAIL")
            with patch("app.film_pipeline_service.start_pipeline") as start:
                start.return_value = {"run": {"id": "run-1"}}
                updated = retry_junction(pid, row["id"])
            self.assertEqual(updated["status"], "REPAIRING")
            self.assertEqual(updated["repair_target_scene"], "SCENE_002")
            start.assert_called_once()
            kwargs = start.call_args.kwargs
            self.assertEqual(kwargs.get("from_scene_id"), "SCENE_002")
            self.assertEqual(kwargs.get("scene_limit"), 1)
            state = get_scene_state(pid, "SCENE_002")
            self.assertEqual(state["status"], "REGENERATING")
            self.assertEqual(state["attempt"], 1)
            self.assertIsNone(state.get("current_job_id"))
        finally:
            delete_film_project(pid)

    def test_junction_retry_creates_new_scene_version(self):
        project = create_film_project(
            "__junction_retry_ver__",
            "Day la kich ban kiem thu junction retry version dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", selected_media_id="m1", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", selected_media_id="m2", attempt=2, current_job_id="job-v2", force=True)
            save_ledger(pid, "SCENE_001", {"accepted_last_frame": "/ok"})
            row = upsert_junction(pid, "SCENE_001", "SCENE_002", status="FAIL")
            with patch("app.film_pipeline_service.start_pipeline", return_value={"run": {"id": "r"}}):
                retry_junction(pid, row["id"])
            state = get_scene_state(pid, "SCENE_002")
            self.assertEqual(state["status"], "REGENERATING")
            self.assertEqual(state["attempt"], 3)
            self.assertIsNone(state.get("current_job_id"))
            self.assertEqual(state.get("selected_media_id"), "m2")
        finally:
            delete_film_project(pid)

    def test_junction_rechecks_after_repair(self):
        project = create_film_project(
            "__junction_recheck__",
            "Day la kich ban kiem thu junction recheck dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", force=True)
            upsert_junction(pid, "SCENE_001", "SCENE_002", status="FAIL")
            with patch("app.film_boundary_service.check_junction") as check:
                check.return_value = {"status": "PASS"}
                updated = recheck_junctions_for_scene(pid, "SCENE_002")
            self.assertEqual(len(updated), 1)
            check.assert_called_once()
        finally:
            delete_film_project(pid)

    def test_junction_stale_after_media_change(self):
        project = create_film_project(
            "__junction_stale2__",
            "Day la kich ban kiem thu junction stale dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            upsert_junction(pid, "SCENE_001", "SCENE_002", status="PASS", selected_previous_media_id="old-a", selected_next_media_id="old-b")
            with patch("app.film_boundary_service._selected_scene_media", side_effect=lambda project_id, scene_id: {"id": "new-" + scene_id}):
                refresh_junction_staleness(pid)
            row = get_pair_junction(pid, "SCENE_001", "SCENE_002")
            self.assertEqual(row["status"], "STALE")
        finally:
            delete_film_project(pid)

    def test_check_junction_does_not_call_vision_when_frame_missing(self):
        project = create_film_project(
            "__junction_novision__",
            "Day la kich ban kiem thu junction missing vision dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", selected_media_id="m1", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", selected_media_id="m2", force=True)
            save_ledger(pid, "SCENE_001", {"accepted_last_frame": "/definitely/not/a/real/frame-a.jpg"})
            save_ledger(pid, "SCENE_002", {"accepted_first_frame": "/definitely/not/a/real/frame-b.jpg"})
            called = []
            with patch("app.film_boundary_service._selected_scene_media", side_effect=lambda _pid, sid: _selected(sid, "m-" + sid)):
                row = check_junction(pid, "SCENE_001", "SCENE_002", vision_fn=lambda evidence: called.append(evidence) or _pass_obs())
            self.assertEqual(row["status"], "BLOCKED")
            self.assertEqual((row.get("qc") or {}).get("code"), "JUNCTION_EVIDENCE_MISSING")
            self.assertEqual(called, [])
        finally:
            delete_film_project(pid)



    def test_dialogue_transition_uses_accepted_audio_ledger_not_image_vision(self):
        base = DATA_DIR / "test_junction_dialogue_ledger"
        prev_frame = _jpeg(base / "prev.jpg")
        next_frame = _jpeg(base / "next.jpg")
        prev_scene = {
            "id": "SCENE_010",
            "characters": ["CHAR_001"],
            "location_id": "LOC_001",
            "dialogue": [{"character_id": "CHAR_001", "text": "A"}],
            "props_present": [],
        }
        next_scene = {
            "id": "SCENE_011",
            "characters": ["CHAR_001"],
            "location_id": "LOC_001",
            "dialogue": [{"character_id": "CHAR_001", "text": "B"}],
            "props_present": [],
        }
        prev_ledger = {
            "accepted_last_frame": str(prev_frame),
            "dialogue_state": "delivered",
            "audio_state": '{"required": true, "present": true, "non_silent": true, "mean_volume_db": -24.0}',
        }
        next_ledger = {
            "accepted_first_frame": str(next_frame),
            "dialogue_state": "delivered",
            "audio_state": '{"required": true, "present": true, "non_silent": true, "mean_volume_db": -25.0}',
        }
        prev_media = _selected("SCENE_010", "m10")
        next_media = _selected("SCENE_011", "m11")
        observations = _pass_obs()
        observations["provider"] = "test"
        observations["model"] = "vision-images-only"

        report = evaluate_junction_qc(
            prev_scene, next_scene,
            previous_ledger=prev_ledger,
            next_ledger=next_ledger,
            previous_media=prev_media,
            next_media=next_media,
            observations=observations,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["dimensions"]["dialogue_transition"]["status"], "passed")
        self.assertTrue(report["dimensions"]["dialogue_transition"]["hard"])
        self.assertEqual((report.get("vision") or {}).get("provider"), "test")
        self.assertNotIn("dialogue_transition", report.get("incomplete_dimensions") or [])

    def test_recover_stale_junctions_restores_only_unchanged_approved_pair(self):
        project = {"scenes": [{"id": "SCENE_001"}, {"id": "SCENE_002"}, {"id": "SCENE_003"}]}
        rows = [
            {
                "previous_scene_id": "SCENE_001",
                "next_scene_id": "SCENE_002",
                "status": "STALE",
                "selected_previous_media_id": "m1",
                "selected_next_media_id": "m2",
                "qc": {"version": "junction-qc-v2", "passed": True},
                "score": 95.0,
            },
            {
                "previous_scene_id": "SCENE_002",
                "next_scene_id": "SCENE_003",
                "status": "STALE",
                "selected_previous_media_id": "m2",
                "selected_next_media_id": "m3",
                "qc": {"version": "junction-qc-v2", "passed": True},
                "score": 96.0,
            },
        ]
        states = {
            "SCENE_001": {"status": "APPROVED"},
            "SCENE_002": {"status": "APPROVED"},
            "SCENE_003": {"status": "STALE"},
        }
        media = {
            "SCENE_001": _selected("SCENE_001", "m1"),
            "SCENE_002": _selected("SCENE_002", "m2"),
            "SCENE_003": _selected("SCENE_003", "m3"),
        }
        with patch("app.film_boundary_service.get_film_project", return_value=project), patch(
            "app.film_boundary_service.list_junctions", return_value=rows
        ), patch(
            "app.film_boundary_service.get_scene_state",
            side_effect=lambda _pid, sid: states[sid],
        ), patch(
            "app.film_boundary_service._selected_scene_media",
            side_effect=lambda _pid, sid: media[sid],
        ), patch(
            "app.film_boundary_service.get_ledger", return_value={}
        ), patch(
            "app.film_boundary_service.resolve_junction_evidence",
            return_value={"ok": True},
        ), patch(
            "app.film_boundary_service.mark_junction",
            return_value={"status": "PASS"},
        ) as mark:
            result = recover_stale_junctions_after_snapshot_rebase("P1")

        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["blocked_count"], 1)
        self.assertEqual(result["blocked"][0]["reasons"], ["NEXT_NOT_APPROVED"])
        mark.assert_called_once()
        self.assertEqual(mark.call_args.args[3], "PASS")

class JunctionAsyncVisionTests(unittest.IsolatedAsyncioTestCase):

    async def test_check_junction_async_awaits_vision_inside_running_loop(self):
        project = create_film_project(
            "__junction_async__",
            "Day la kich ban kiem thu async junction vision dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", selected_media_id="m1", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", selected_media_id="m2", force=True)
            frame_a = _jpeg(DATA_DIR / "test_junction_async" / "a.jpg")
            frame_b = _jpeg(DATA_DIR / "test_junction_async" / "b.jpg")
            save_ledger(pid, "SCENE_001", {"accepted_last_frame": str(frame_a)})
            save_ledger(pid, "SCENE_002", {"accepted_first_frame": str(frame_b)})
            calls = []

            async def fake_vision(evidence):
                calls.append(evidence)
                payload = _pass_obs()
                payload["provider"] = "test"
                payload["model"] = "vision-async"
                return payload

            with patch("app.film_boundary_service._selected_scene_media", side_effect=lambda _pid, sid: _selected(sid, "m1" if sid == "SCENE_001" else "m2")):
                row = await check_junction_async(pid, "SCENE_001", "SCENE_002", vision_fn=fake_vision)

            self.assertEqual(row["status"], "PASS")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("JUNCTION_VISION_LOOP", str(row))
            self.assertEqual(((row.get("qc") or {}).get("vision") or {}).get("provider"), "test")
            self.assertEqual(((row.get("qc") or {}).get("vision") or {}).get("model"), "vision-async")
        finally:
            delete_film_project(pid)

    async def test_recheck_junctions_for_scene_async_runs_inside_loop(self):
        project = create_film_project(
            "__junction_async_recheck__",
            "Day la kich ban kiem thu async junction recheck dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", force=True)
            upsert_junction(pid, "SCENE_001", "SCENE_002", status="FAIL")

            async def fake_check(*args, **kwargs):
                return {"status": "PASS"}

            with patch("app.film_boundary_service.check_junction_async", side_effect=fake_check) as check:
                updated = await recheck_junctions_for_scene_async(pid, "SCENE_002")

            self.assertEqual(len(updated), 1)
            check.assert_awaited_once()
        finally:
            delete_film_project(pid)

