import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from .data_isolation import IsolatedDataTestCase
from .desktop_network import requires_internet
from .main import app


class NarratorApiTests(IsolatedDataTestCase):
    def test_saved_speaker_calibration_route(self):
        calibration = {
            "status": "calibrated",
            "character_status": "calibrated",
            "narrator_status": "calibrated",
            "speaker_count": 3,
            "speaker_calibrations": {
                "NARRATOR": {"speaker_id": "NARRATOR", "status": "calibrated", "threshold": 0.47, "gap": 0.42}
            },
        }
        with (
            patch("app.api.get_film_project", return_value={"id": "P1"}),
            patch("app.api.load_project_calibration", return_value=calibration) as load_calibration,
            TestClient(app) as client,
        ):
            response = client.get("/api/film/projects/P1/speaker/calibration")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["calibration"]["narrator_status"], "calibrated")
        load_calibration.assert_called_once_with("P1")

    def test_narrator_routes_require_internet(self):
        self.assertTrue(requires_internet("POST", "/api/film/projects/P1/narrator/preview"))
        self.assertTrue(requires_internet("POST", "/api/film/projects/P1/narrator/apply"))

    def test_preview_route_contract(self):
        report = {
            "version": "narrator-tts-overlay-v1",
            "project_id": "P1",
            "provider": "xkiro",
            "model": "tts-1",
            "voice_id": "voice-fixed",
            "scene_count": 2,
            "same_voice_pairs": [{"scene_a": "S1", "scene_b": "S2", "similarity": 0.8}],
            "min_same_voice_similarity": 0.8,
            "required_similarity": 0.6,
            "ready": True,
            "scenes": [{"scene_id": "S1"}, {"scene_id": "S2"}],
        }
        with (
            patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "online"}, clear=False),
            patch("app.api.get_film_project", return_value={"id": "P1"}),
            patch("app.api.preview_narrator_upgrade", return_value=report) as preview,
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/film/projects/P1/narrator/preview",
                json={"voice_id": "voice-fixed", "speed": 1.05},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        preview.assert_called_once_with("P1", voice_id="voice-fixed", speed=1.05)

    def test_apply_route_contract(self):
        result = {
            "version": "narrator-tts-overlay-v1",
            "project_id": "P1",
            "voice_id": "voice-fixed",
            "provider": "xkiro",
            "model": "tts-1",
            "preview": {"ready": True},
            "speaker_acceptance": {
                "project_id": "P1",
                "speech_scenes": 2,
                "enrolled_references": 1,
                "verified_scenes": 2,
                "failed_scenes": 0,
                "blocked_scenes": 0,
                "passed": True,
                "fully_verified": True,
                "items": [],
            },
            "upgraded_scenes": [{"scene_id": "S1"}, {"scene_id": "S2"}],
            "junctions": [],
            "final": {"project_id": "P1", "current": {"status": "APPROVED"}},
        }
        apply_mock = AsyncMock(return_value=result)
        with (
            patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "online"}, clear=False),
            patch("app.api.get_film_project", return_value={"id": "P1"}),
            patch("app.api.apply_narrator_upgrade", apply_mock),
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/film/projects/P1/narrator/apply",
                json={"voice_id": "voice-fixed", "speed": 1.0},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["speaker_acceptance"]["fully_verified"])
        apply_mock.assert_awaited_once_with("P1", voice_id="voice-fixed", speed=1.0)

    def test_preview_validation_blocks_invalid_speed(self):
        with patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "online"}, clear=False), TestClient(app) as client:
            response = client.post(
                "/api/film/projects/P1/narrator/preview",
                json={"voice_id": "voice-fixed", "speed": 3.0},
            )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
