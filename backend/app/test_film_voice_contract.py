import unittest

from .db import init_db
from .film_acceptance_snapshot import _voice_ids_for_scene
from .film_audio_schema import normalize_audio_requirements
from .film_compiler import compile_flow_prompt
from .film_qc_service import _speaker_status_requires_block
from .film_store import create_film_project, delete_film_project
from .film_voice_profile_store import ensure_voice_profiles, get_voice_profile


class VoiceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_voiceover_is_always_a_narrator_lane(self):
        req = normalize_audio_requirements(
            {
                "id": "SCENE_001",
                "characters": ["CHAR_001"],
                "dialogue": [{"character_id": "CHAR_001", "text": "Xin chao"}],
                "voiceover": "Loi dan",
            },
            {"characters": [{"id": "CHAR_001", "name": "An"}]},
        )
        self.assertEqual(req["speakers"], ["CHAR_001", "NARRATOR"])
    def test_voiceover_adds_narrator_to_snapshot_voice_ids(self):
        voice_ids = _voice_ids_for_scene(
            {"characters": ["CHAR_002"], "voiceover": "Loi dan"}
        )
        self.assertEqual(voice_ids, {"CHAR_002", "NARRATOR"})

    def test_narrator_profile_is_persisted(self):
        project = create_film_project(
            "__voice_narrator__",
            "Day la kich ban kiem thu narrator voice profile dai hon hai muoi ky tu.",
            "xkiro",
            "test-model",
            {"scene_duration": 8},
        )
        pid = project["id"]
        try:
            ensure_voice_profiles(
                {
                    "id": pid,
                    "characters": [],
                    "scenes": [{"id": "SCENE_001", "voiceover": "Loi dan"}],
                }
            )
            profile = get_voice_profile(pid, "NARRATOR")
            self.assertIsNotNone(profile)
            payload = (profile or {}).get("profile") or {}
            self.assertEqual(payload.get("voice_profile_id"), "VOICE_NARRATOR")
            self.assertEqual(payload.get("gender"), "neutral")
        finally:
            delete_film_project(pid)

    def test_compiler_includes_narrator_voice_canon(self):
        scene = {
            "id": "SCENE_001",
            "duration": 8,
            "characters": [],
            "location_id": None,
            "props_present": [],
            "action": "Mot canh im lang.",
            "dialogue": [],
            "voiceover": "Loi dan",
            "start_state": "start",
            "end_state": "end",
        }
        prompt, meta = compile_flow_prompt(scene, "Cinematic", [], [], [])
        self.assertIn("VOICE_NARRATOR", prompt)
        self.assertIn("same adult Vietnamese narrator", prompt)
        self.assertEqual(meta["compiler"], "deterministic_flow_prompt_v4_narrator_lock")

    def test_required_speaker_states_fail_closed(self):
        self.assertFalse(_speaker_status_requires_block({"status": "passed"}))
        self.assertFalse(_speaker_status_requires_block({"status": "enrolled_reference"}))
        self.assertTrue(_speaker_status_requires_block({"status": "calibration_ambiguous"}))
        self.assertTrue(_speaker_status_requires_block({"status": "calibration_pending"}))
        self.assertTrue(_speaker_status_requires_block({"status": "not_evaluated"}))
        self.assertTrue(_speaker_status_requires_block({"status": "error"}))


if __name__ == "__main__":
    unittest.main()
