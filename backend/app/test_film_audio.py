import unittest

from .db import init_db
from .film_audio_qc import evaluate_audio_qc
from .film_audio_schema import normalize_audio_requirements, normalize_dialogue_line, speech_required
from .film_dialogue_service import dialogue_is_valid, scene_dialogue_requirements
from .film_scene_qc_v2 import evaluate_qc_v2
from .film_store import create_film_project, delete_film_project
from .film_voice_profile_store import get_voice_profile, upsert_voice_profile


class AudioContractTests(unittest.TestCase):
    def test_dialogue_maps_to_character_id(self):
        project = {"characters": [{"id": "CHAR_001", "name": "An"}]}
        mapped = normalize_dialogue_line({"character_id": "CHAR_001", "text": "Xin chao"}, project)
        self.assertEqual(mapped["speaker_character_id"], "CHAR_001")
        named = normalize_dialogue_line({"speaker": "An", "text": "Xin chao"}, project)
        self.assertEqual(named["speaker_character_id"], "CHAR_001")

    def test_missing_dialogue_fails_when_required(self):
        scene = {
            "id": "SCENE_A",
            "characters": ["CHAR_001"],
            "dialogue": [{"speaker_character_id": "CHAR_001", "text": ""}],
        }
        audio = evaluate_audio_qc(scene, {"audio_check": {"present": True, "non_silent": True}})
        self.assertTrue(audio["required"])
        self.assertFalse(audio["dimensions"]["dialogue_presence"]["passed"])

    def test_wrong_speaker_fails(self):
        scene = {
            "id": "SCENE_A",
            "characters": ["CHAR_001"],
            "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Xin chao"}],
        }
        audio = evaluate_audio_qc(
            scene,
            {
                "audio_check": {"present": True, "non_silent": True},
                "speech_check": {"passed": True, "speaker_character_id": "CHAR_002", "transcript": "Xin chao"},
            },
            {"id": "p", "characters": [{"id": "CHAR_001", "name": "An"}]},
        )
        failed = audio.get("hard_failed") or []
        self.assertTrue("speaker_correctness" in failed or audio["dimensions"].get("speaker_correctness", {}).get("passed") is False)

    def test_voice_profile_persists(self):
        init_db()
        project = create_film_project(
            "__audio_voice__",
            "Day la kich ban kiem thu voice profile dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            saved = upsert_voice_profile(pid, "CHAR_001", {"gender": "female", "language": "vi"})
            loaded = get_voice_profile(pid, "CHAR_001")
            self.assertEqual(loaded["character_id"], "CHAR_001")
            self.assertEqual((loaded.get("profile") or {}).get("gender"), "female")
            self.assertEqual(saved["id"], loaded["id"])
        finally:
            delete_film_project(pid)

    def test_voice_drift_fails(self):
        init_db()
        project = create_film_project(
            "__audio_drift__",
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
        finally:
            delete_film_project(pid)

    def test_voice_continuity_is_not_evaluated_without_acoustic_evidence(self):
        init_db()
        project = create_film_project(
            "__audio_no_fake_voice_score__",
            "Day la kich ban kiem thu khong tu gan diem voice continuity.",
            "xkiro",
            "test-model",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            upsert_voice_profile(pid, "CHAR_001", {"gender": "female"})
            scene = {
                "id": "SCENE_A",
                "project_id": pid,
                "characters": ["CHAR_001"],
                "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Xin chao"}],
            }
            audio = evaluate_audio_qc(
                scene,
                {
                    "audio_check": {"present": True, "non_silent": True},
                    "speech_check": {"passed": True, "transcript": "Xin chao"},
                },
                {"id": pid, "characters": [{"id": "CHAR_001", "name": "An"}]},
            )
            continuity = audio["dimensions"]["voice_continuity"]
            self.assertEqual(continuity["status"], "not_evaluated")
            self.assertIsNone(continuity["score"])
            self.assertEqual(continuity["issue"], "ACOUSTIC_SPEAKER_EVIDENCE_MISSING")
        finally:
            delete_film_project(pid)

    def test_voice_similarity_is_real_continuity_evidence(self):
        init_db()
        project = create_film_project(
            "__audio_voice_similarity__",
            "Day la kich ban kiem thu speaker similarity cho voice continuity.",
            "xkiro",
            "test-model",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            upsert_voice_profile(pid, "CHAR_001", {"gender": "female"})
            scene = {
                "id": "SCENE_A",
                "project_id": pid,
                "characters": ["CHAR_001"],
                "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Xin chao"}],
            }
            passed = evaluate_audio_qc(
                scene,
                {
                    "audio_check": {"present": True, "non_silent": True},
                    "speech_check": {
                        "passed": True,
                        "transcript": "Xin chao",
                        "speaker_similarity": 0.91,
                    },
                },
                {"id": pid, "characters": [{"id": "CHAR_001", "name": "An"}]},
            )
            continuity = passed["dimensions"]["voice_continuity"]
            self.assertTrue(continuity["passed"])
            self.assertEqual(continuity["score"], 91.0)

            failed = evaluate_audio_qc(
                scene,
                {
                    "audio_check": {"present": True, "non_silent": True},
                    "speech_check": {
                        "passed": True,
                        "transcript": "Xin chao",
                        "speaker_similarity": 0.42,
                    },
                },
                {"id": pid, "characters": [{"id": "CHAR_001", "name": "An"}]},
            )
            self.assertIn("voice_continuity", failed["hard_failed"])
        finally:
            delete_film_project(pid)

    def test_voice_similarity_uses_verifier_threshold(self):
        scene = {
            "id": "SCENE_A",
            "project_id": "p",
            "characters": ["CHAR_001"],
            "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Xin chao"}],
        }
        audio = evaluate_audio_qc(
            scene,
            {
                "audio_check": {"present": True, "non_silent": True},
                "speech_check": {
                    "passed": True,
                    "transcript": "Xin chao",
                    "speaker_similarity": 0.34,
                    "speaker_verification": {
                        "threshold": 0.23709,
                        "status": "passed",
                    },
                },
            },
            {"id": "p", "characters": [{"id": "CHAR_001", "name": "An"}]},
        )
        continuity = audio["dimensions"]["voice_continuity"]
        self.assertTrue(continuity["passed"])
        self.assertAlmostEqual(continuity["threshold"], 23.709, places=3)

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

    def test_audio_qc_merge_blocks_video_qc_on_hard_failure(self):
        scene = {
            "id": "SCENE_A",
            "characters": ["CHAR_001"],
            "dialogue": [{"speaker_character_id": "CHAR_001", "text": "Xin chao"}],
        }
        result = evaluate_qc_v2(
            {
                "qc_status": "passed",
                "consistency_score": 93,
                "qc": {
                    "passed": True,
                    "consistency_score": 93,
                    "dimension_scores": {"identity": 96, "wardrobe": 91, "camera": 80, "lighting": 80},
                    "audio_check": {"present": False, "non_silent": False},
                },
            },
            scene,
        )
        self.assertEqual(result["qc_status"], "failed")
        hard = result["qc"]["hard_gate"]["failed"]
        self.assertTrue("audio" in hard or "dialogue_presence" in hard or "speech" in hard)

    def test_voiceover_uses_narrator_profile(self):
        req = normalize_audio_requirements(
            {"id": "SCENE_VO", "characters": ["CHAR_001"], "dialogue": [], "voiceover": "Loi dan"},
            {"id": "p", "characters": [{"id": "CHAR_001", "name": "An"}]},
        )
        self.assertEqual(req["speakers"], ["NARRATOR"])
        self.assertEqual(req["voice_profile_ids"], ["VOICE_NARRATOR"])
