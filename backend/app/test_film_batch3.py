import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from .config import DATA_DIR
from .data_isolation import IsolatedDataMixin, IsolatedDataTestCase
from .db import init_db
from .film_audio_qc import evaluate_audio_qc
from .film_audio_schema import normalize_dialogue_line, speech_required
from .film_boundary_qc import evaluate_junction_qc, junction_repair_target, selected_media_usable
from .film_boundary_service import check_junction, ensure_junctions, refresh_junction_staleness, retry_junction
from .film_boundary_store import upsert_junction, get_pair_junction
from .film_dialogue_service import dialogue_is_valid, scene_dialogue_requirements
from .film_scene_qc_v2 import evaluate_qc_v2
from .film_store import create_film_project, delete_film_project
from .film_voice_profile_store import get_voice_profile, upsert_voice_profile


def _selected(scene_id, media_id):
    return {"id": media_id, "scene_id": scene_id, "is_selected": True, "status": "completed", "qc_status": "passed"}


def _frame(name: str) -> str:
    path = DATA_DIR / "generated_media" / "_batch3_junc" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (110, 40, 40)).save(path, "JPEG")
    return str(path)


def _media_frame(scene_id, media_id, frame: str):
    return {**_selected(scene_id, media_id), "metadata": {"last_frame_path": frame, "first_frame_path": frame}}


def _pass_obs():
    names = (
        "identity", "wardrobe", "character_position", "body_orientation",
        "prop_holder", "prop_state", "prop_owner", "location_geometry",
        "lighting", "camera_direction", "motion_direction", "audio_transition", "dialogue_transition",
    )
    return {name: {"score": 96, "passed": True, "evidence": "match"} for name in names}


class DialogueSchemaTests(unittest.TestCase):
    def test_dialogue_maps_to_character_id(self):
        project = {"characters": [{"id": "CHAR_001", "name": "An"}]}
        mapped = normalize_dialogue_line({"character_id": "CHAR_001", "text": "Xin chao"}, project)
        self.assertEqual(mapped["speaker_character_id"], "CHAR_001")
        named = normalize_dialogue_line({"speaker": "An", "text": "Xin chao"}, project)
        self.assertEqual(named["speaker_character_id"], "CHAR_001")
        unknown = normalize_dialogue_line({"speaker": "Nguoi la", "text": "Hey"}, project)
        self.assertIsNone(unknown["speaker_character_id"])
        self.assertEqual(unknown["error"], "UNKNOWN_SPEAKER")

    def test_dialogue_scene_requires_speaker(self):
        scene = {"id": "SCENE_A", "characters": ["CHAR_001"], "dialogue": [{"text": "Xin chao"}]}
        req = scene_dialogue_requirements({"characters": [{"id": "CHAR_001", "name": "An"}]}, scene)
        self.assertTrue(req["speech_required"])
        self.assertEqual(req["dialogue"][0]["error"], "DIALOGUE_SPEAKER_MISSING")
        ok, errors = dialogue_is_valid(scene, {"characters": [{"id": "CHAR_001", "name": "An"}]})
        self.assertFalse(ok)
        self.assertTrue(any(item["code"] == "DIALOGUE_SPEAKER_MISSING" for item in errors))

    def test_missing_dialogue_fails_when_required(self):
        scene = {
            "id": "SCENE_A",
            "characters": ["CHAR_001"],
            "dialogue": [{"speaker_character_id": "CHAR_001", "text": ""}],
        }
        audio = evaluate_audio_qc(scene, {"audio_check": {"present": True, "non_silent": True}})
        self.assertTrue(audio["required"])
        self.assertFalse(audio["dimensions"]["dialogue_presence"]["passed"])
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 93,
                "qc": {
                    "passed": True,
                    "consistency_score": 93,
                    "dimension_scores": {"identity": 96, "wardrobe": 91, "camera": 80, "lighting": 80},
                    "audio_check": {"present": True, "non_silent": True},
                },
            },
            scene,
        )
        self.assertEqual(result["qc_status"], "failed")
        self.assertIn("dialogue_presence", result["qc"]["hard_gate"]["failed"])

    def test_scene_without_dialogue_marks_speech_not_required(self):
        self.assertFalse(speech_required({"characters": ["CHAR_001"], "dialogue": []}))
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 93,
                "qc": {
                    "passed": True,
                    "consistency_score": 93,
                    "dimension_scores": {"identity": 96, "wardrobe": 91, "camera": 80, "lighting": 80},
                },
            },
            {"characters": ["CHAR_001"]},
        )
        speech = result["qc"]["dimensions"]["speech"]
        self.assertEqual(speech["status"], "not_required")
        self.assertIsNone(speech["passed"])
        self.assertNotIn("speech", result["qc"].get("incomplete_dimensions") or [])


class VoiceProfileTests(IsolatedDataTestCase):
    def test_voice_profile_persists(self):
        init_db()
        project = create_film_project(
            "__voice_prof__",
            "Day la kich ban kiem thu voice profile dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            saved = upsert_voice_profile(pid, "CHAR_001", {"gender": "female", "language": "vi", "pitch": "medium-high"})
            loaded = get_voice_profile(pid, "CHAR_001")
            self.assertEqual(loaded["character_id"], "CHAR_001")
            self.assertEqual((loaded.get("profile") or {}).get("gender"), "female")
            self.assertEqual(saved["id"], loaded["id"])
        finally:
            delete_film_project(pid)

    def test_voice_drift_hard_gate(self):
        init_db()
        project = create_film_project(
            "__voice_drift__",
            "Day la kich ban kiem thu voice drift dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            upsert_voice_profile(pid, "CHAR_001", {"gender": "female"})
            scene = {
                "id": "SCENE_A",
                "project_id": pid,
                "characters": ["CHAR_001"],
                "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Chau mang dong ho."}],
            }
            audio = evaluate_audio_qc(
                scene,
                {"audio_check": {"present": True, "non_silent": True}, "speech_check": {"passed": True, "gender": "male", "transcript": "Chau mang dong ho."}},
                {"id": pid, "characters": [{"id": "CHAR_001", "name": "An", "gender": "nu"}]},
            )
            self.assertIn("voice_continuity", audio["hard_failed"])
            self.assertFalse(audio["dimensions"]["voice_continuity"]["passed"])
        finally:
            delete_film_project(pid)


class JunctionQcTests(IsolatedDataTestCase):
    def test_junction_pass(self):
        called = []
        prev = {"id": "SCENE_001", "characters": ["CHAR_001"], "location_id": "LOC_001", "props_present": ["PROP_001"]}
        nxt = {"id": "SCENE_002", "characters": ["CHAR_001"], "location_id": "LOC_001", "props_present": ["PROP_001"]}
        report = evaluate_junction_qc(
            prev, nxt,
            previous_ledger={"character_visibility": {"CHAR_001": True}, "prop_holder": {"PROP_001": "CHAR_001"}, "accepted_last_frame": "/definitely/not/a/real/frame-a.jpg"},
            next_ledger={"prop_holder": {"PROP_001": "CHAR_001"}, "accepted_first_frame": "/definitely/not/a/real/frame-b.jpg"},
            previous_media=_selected("SCENE_001", "m1"),
            next_media=_selected("SCENE_002", "m2"),
            vision_fn=lambda evidence: called.append(True) or {"identity": {"score": 96}},
        )
        self.assertTrue(report["blocked"])
        self.assertFalse(report["passed"])
        self.assertEqual(report["code"], "JUNCTION_EVIDENCE_MISSING")
        self.assertEqual(called, [])

    def test_junction_identity_fail(self):
        prev_frame, next_frame = _frame("id_prev.jpg"), _frame("id_next.jpg")
        prev = {"id": "SCENE_001", "characters": ["CHAR_001"], "location_id": "LOC_001"}
        nxt = {"id": "SCENE_002", "characters": ["CHAR_001"], "location_id": "LOC_001"}
        obs = _pass_obs()
        obs["identity"] = {"score": 40, "passed": False, "evidence": "different face"}
        report = evaluate_junction_qc(
            prev, nxt,
            previous_ledger={"character_visibility": {"CHAR_001": False}, "accepted_last_frame": prev_frame},
            next_ledger={"accepted_first_frame": next_frame},
            previous_media=_media_frame("SCENE_001", "m1", prev_frame),
            next_media=_media_frame("SCENE_002", "m2", next_frame),
            observations=obs,
        )
        self.assertFalse(report["passed"])
        self.assertIn("identity", report["hard_gate"]["failed"])

    def test_junction_prop_transfer_fail(self):
        prev_frame, next_frame = _frame("prop_prev.jpg"), _frame("prop_next.jpg")
        prev = {"id": "SCENE_001", "characters": ["CHAR_001"], "props_present": ["PROP_001"], "continuity": {"critical_props": ["PROP_001"]}}
        nxt = {"id": "SCENE_002", "characters": ["CHAR_001", "CHAR_002"], "props_present": ["PROP_001"], "prop_transfers": [], "continuity": {"critical_props": ["PROP_001"]}}
        report = evaluate_junction_qc(
            prev, nxt,
            previous_ledger={"character_visibility": {"CHAR_001": True}, "prop_holder": {"PROP_001": "CHAR_001"}, "accepted_last_frame": prev_frame},
            next_ledger={"prop_holder": {"PROP_001": "CHAR_002"}, "accepted_first_frame": next_frame},
            previous_media=_media_frame("SCENE_001", "m1", prev_frame),
            next_media=_media_frame("SCENE_002", "m2", next_frame),
            observations=_pass_obs(),
        )
        self.assertFalse(report["passed"])
        self.assertTrue({"prop_state", "prop_holder"} & set(report["hard_gate"]["failed"]))

    def test_junction_uses_selected_media_only(self):
        prev = {"id": "SCENE_001", "characters": ["CHAR_001"]}
        nxt = {"id": "SCENE_002", "characters": ["CHAR_001"]}
        unselected = {"id": "old", "scene_id": "SCENE_001", "is_selected": False, "status": "completed", "qc_status": "passed"}
        ok, code = selected_media_usable(unselected, "SCENE_001")
        self.assertFalse(ok)
        self.assertEqual(code, "JUNCTION_SELECTED_MEDIA_REQUIRED")
        report = evaluate_junction_qc(prev, nxt, previous_media=unselected, next_media=_selected("SCENE_002", "m2"))
        self.assertTrue(report["blocked"])
        self.assertEqual(report["code"], "JUNCTION_SELECTED_MEDIA_REQUIRED")

    def test_junction_regenerates_next_scene_first(self):
        target = junction_repair_target(
            {"id": "SCENE_001"}, {"id": "SCENE_002"},
            {"accepted_last_frame": "/last"},
        )
        self.assertEqual(target["scene_id"], "SCENE_002")
        self.assertEqual(target["prefer"], "next")
        broken = junction_repair_target({"id": "SCENE_001"}, {"id": "SCENE_002"}, {})
        self.assertEqual(broken["prefer"], "previous")

    def test_junction_stale_after_scene_version_change(self):
        init_db()
        project = create_film_project(
            "__junction_stale__",
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

    def test_junction_retry_policy_does_not_start_pipeline(self):
        init_db()
        project = create_film_project(
            "__junction_retry__",
            "Day la kich ban kiem thu junction retry dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            from .film_store import append_film_scenes
            from .film_scene_state_store import save_ledger, upsert_scene_state, get_scene_state
            append_film_scenes(pid, [
                {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
                {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            ], start_index=0)
            upsert_scene_state(pid, "SCENE_001", 0, status="APPROVED", force=True)
            upsert_scene_state(pid, "SCENE_002", 1, status="APPROVED", attempt=0, current_job_id="old", force=True)
            save_ledger(pid, "SCENE_001", {"accepted_last_frame": "/ok"})
            row = upsert_junction(pid, "SCENE_001", "SCENE_002", status="FAIL")
            with patch("app.film_pipeline_service.start_pipeline") as start:
                start.return_value = {"run": {"id": "run-1"}}
                updated = retry_junction(pid, row["id"])
            self.assertEqual(updated["status"], "REPAIRING")
            self.assertIn(updated["repair"]["prefer"], {"next", "previous"})
            start.assert_called_once()
            self.assertEqual(start.call_args.kwargs.get("from_scene_id"), "SCENE_002")
            self.assertEqual(start.call_args.kwargs.get("scene_limit"), 1)
            self.assertEqual(get_scene_state(pid, "SCENE_002")["status"], "REGENERATING")
        finally:
            delete_film_project(pid)
