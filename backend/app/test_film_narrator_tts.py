import unittest
from unittest.mock import patch

import numpy as np

from .film_narrator_tts_service import (
    SPEAKER_THRESHOLD,
    atempo_chain,
    preview_narrator_upgrade,
    speech_windows_from_stt,
)


class NarratorTtsPureTests(unittest.TestCase):
    def test_atempo_chain_supports_full_range(self):
        self.assertEqual(atempo_chain(1.0), "atempo=1.000000")
        self.assertEqual(atempo_chain(0.25), "atempo=0.5,atempo=0.500000")
        self.assertEqual(atempo_chain(4.0), "atempo=2.0,atempo=2.000000")

    def test_speech_windows_merge_nearby_word_regions(self):
        stt = {
            "segments": [
                {
                    "start": 1.0,
                    "end": 1.5,
                    "words": [
                        {"start": 1.0, "end": 1.2},
                        {"start": 1.25, "end": 1.5},
                    ],
                },
                {
                    "start": 1.6,
                    "end": 2.0,
                    "words": [
                        {"start": 1.6, "end": 2.0},
                    ],
                },
            ]
        }
        self.assertEqual(
            speech_windows_from_stt(stt, 8.0, pad_seconds=0.1),
            [(0.9, 2.1)],
        )

    def test_speech_windows_clamp_to_duration(self):
        stt = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 7.95,
                    "words": [
                        {"start": 0.02, "end": 7.95},
                    ],
                }
            ]
        }
        self.assertEqual(
            speech_windows_from_stt(stt, 8.0, pad_seconds=0.2),
            [(0.0, 8.0)],
        )


class NarratorTtsPreviewTests(unittest.TestCase):
    def _project(self):
        return {
            "id": "P1",
            "scenes": [
                {"id": "SCENE_007", "voiceover": "Một", "dialogue": []},
                {"id": "SCENE_015", "voiceover": "Hai", "dialogue": []},
            ],
        }

    def _row(self, sid, embedding):
        return {
            "scene_id": sid,
            "video_stream_unchanged": True,
            "new_stt": {"passed": True},
            "audio_metrics": {"non_silent": True},
            "_embedding": np.asarray(embedding, dtype=np.float32),
        }

    @patch("app.film_narrator_tts_service.normalize_audio_requirements")
    @patch("app.film_narrator_tts_service.compose_narrator_audio")
    @patch("app.film_narrator_tts_service.get_film_project")
    def test_preview_ready_when_same_voice_is_separable(
        self, get_project, compose, normalize
    ):
        get_project.return_value = self._project()
        normalize.return_value = {"speakers": ["NARRATOR"], "speech_required": True}
        compose.side_effect = [
            self._row("SCENE_007", [1.0, 0.0, 0.0]),
            self._row("SCENE_015", [0.8, 0.1, 0.0]),
        ]
        report = preview_narrator_upgrade("P1", voice_id="voice-fixed")
        self.assertTrue(report["ready"])
        self.assertGreaterEqual(report["min_same_voice_similarity"], SPEAKER_THRESHOLD)
        self.assertNotIn("_embedding", report["scenes"][0])

    @patch("app.film_narrator_tts_service.normalize_audio_requirements")
    @patch("app.film_narrator_tts_service.compose_narrator_audio")
    @patch("app.film_narrator_tts_service.get_film_project")
    def test_preview_blocks_low_same_voice_similarity(
        self, get_project, compose, normalize
    ):
        get_project.return_value = self._project()
        normalize.return_value = {"speakers": ["NARRATOR"], "speech_required": True}
        compose.side_effect = [
            self._row("SCENE_007", [1.0, 0.0]),
            self._row("SCENE_015", [0.0, 1.0]),
        ]
        report = preview_narrator_upgrade("P1", voice_id="voice-fixed")
        self.assertFalse(report["ready"])
        self.assertLess(report["min_same_voice_similarity"], SPEAKER_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
