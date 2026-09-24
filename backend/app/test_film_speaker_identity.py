import unittest
from unittest.mock import patch

import numpy as np

from .data_isolation import IsolatedDataMixin, IsolatedDataTestCase
from .db import init_db
from .film_speaker_identity import (
    SPEAKER_THRESHOLD,
    cosine_similarity,
    reset_project_speaker_calibration,
    reset_speaker_reference,
    save_project_calibration,
    verify_or_enroll_scene_speaker,
)
from .film_store import create_film_project, delete_film_project, save_film_bible
from .film_voice_profile_store import get_voice_profile


class SpeakerIdentityTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        init_db()

    def setUp(self):
        self.project = create_film_project(
            "__speaker_identity_test__",
            "Day la kich ban kiem thu speaker identity dai hon hai muoi ky tu.",
            "xkiro",
            "test-model",
            {"scene_duration": 8},
        )
        save_film_bible(
            self.project["id"],
            {
                "project_title": "__speaker_identity_test__",
                "characters": [
                    {"id": "CHAR_001", "name": "An", "gender": "female"},
                    {"id": "CHAR_002", "name": "Quang", "gender": "male"},
                ],
                "locations": [],
                "props": [],
            },
        )
        self.project["characters"] = [
            {"id": "CHAR_001", "name": "An", "gender": "female"},
            {"id": "CHAR_002", "name": "Quang", "gender": "male"},
        ]
        self.scene = {
            "id": "SCENE_001",
            "project_id": self.project["id"],
            "characters": ["CHAR_001"],
            "dialogue": [
                {"speaker_character_id": "CHAR_001", "text": "Xin chao"}
            ],
        }
        self.speech = {
            "available": True,
            "passed": True,
            "transcript": "Xin chao",
            "segments": [{"start": 0.2, "end": 1.8, "text": "Xin chao"}],
        }

    def tearDown(self):
        reset_speaker_reference(self.project["id"], "CHAR_001")
        reset_project_speaker_calibration(self.project["id"])
        delete_film_project(self.project["id"])

    def test_cosine_similarity(self):
        self.assertAlmostEqual(
            cosine_similarity(
                np.asarray([1.0, 0.0], dtype=np.float32),
                np.asarray([0.8, 0.6], dtype=np.float32),
            ),
            0.8,
            places=5,
        )

    @patch("app.film_speaker_identity.speaker_embedding_from_video")
    def test_enroll_then_verify_and_fail(self, mocked):
        mocked.return_value = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
        enrolled = verify_or_enroll_scene_speaker(
            self.project, self.scene, "dummy.mp4", self.speech
        )
        self.assertEqual(enrolled["status"], "enrolled_reference")
        self.assertIsNone(enrolled["speaker_similarity"])

        profile = get_voice_profile(self.project["id"], "CHAR_001")
        acoustic = (profile.get("profile") or {}).get("acoustic_identity") or {}
        self.assertEqual(acoustic.get("enrolled_scene_id"), "SCENE_001")
        self.assertNotIn("embedding", acoustic)
        self.assertTrue(acoustic.get("embedding_sha256"))

        save_project_calibration(
            self.project["id"],
            {
                "status": "calibrated",
                "threshold": 0.6,
                "positive_min": 0.8,
                "negative_max": 0.2,
                "gap": 0.6,
            },
        )

        mocked.return_value = np.asarray([0.8, 0.6, 0.0], dtype=np.float32)
        passed = verify_or_enroll_scene_speaker(
            self.project,
            {**self.scene, "id": "SCENE_002"},
            "dummy.mp4",
            self.speech,
        )
        self.assertEqual(passed["status"], "passed")
        self.assertTrue(passed["passed"])
        self.assertGreaterEqual(passed["speaker_similarity"], SPEAKER_THRESHOLD)

        mocked.return_value = np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
        failed = verify_or_enroll_scene_speaker(
            self.project,
            {**self.scene, "id": "SCENE_003"},
            "dummy.mp4",
            self.speech,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["passed"])
        self.assertLess(failed["speaker_similarity"], SPEAKER_THRESHOLD)

    @patch("app.film_speaker_identity.speaker_embedding_from_video")
    def test_ambiguous_calibration_does_not_enforce_fallback(self, mocked):
        mocked.return_value = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
        first = verify_or_enroll_scene_speaker(
            self.project, self.scene, "dummy.mp4", self.speech
        )
        self.assertEqual(first["status"], "enrolled_reference")

        save_project_calibration(
            self.project["id"],
            {
                "status": "partial",
                "threshold": None,
                "speaker_calibrations": {
                    "CHAR_001": {
                        "status": "ambiguous",
                        "threshold": None,
                    }
                },
            },
        )

        mocked.return_value = np.asarray([0.2, 0.98, 0.0], dtype=np.float32)
        result = verify_or_enroll_scene_speaker(
            self.project,
            {**self.scene, "id": "SCENE_002"},
            "dummy.mp4",
            self.speech,
        )
        self.assertEqual(result["status"], "calibration_ambiguous")
        self.assertIsNone(result["speaker_similarity"])
        self.assertIsNotNone(result["raw_similarity"])
        self.assertIsNone(result["passed"])
        self.assertFalse(result["calibrated"])

    def test_multi_speaker_requires_diarization(self):
        scene = {
            "id": "SCENE_MULTI",
            "project_id": self.project["id"],
            "characters": ["CHAR_001", "CHAR_002"],
            "dialogue": [
                {"speaker_character_id": "CHAR_001", "text": "Xin chao"},
                {"speaker_character_id": "CHAR_002", "text": "Chao ban"},
            ],
        }
        result = verify_or_enroll_scene_speaker(
            self.project, scene, "dummy.mp4", self.speech
        )
        self.assertEqual(result["status"], "not_evaluated")
        self.assertEqual(result["error"], "MULTI_SPEAKER_DIARIZATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
