import unittest

import numpy as np

from .film_speaker_calibration import build_speaker_calibration


class SpeakerCalibrationTests(unittest.TestCase):
    def test_calibration_requires_clean_margin(self):
        samples = [
            {"scene_id": "A1", "speaker_character_id": "CHAR_A", "embedding": np.asarray([1.0, 0.0], dtype=np.float32)},
            {"scene_id": "A2", "speaker_character_id": "CHAR_A", "embedding": np.asarray([0.98, 0.20], dtype=np.float32)},
            {"scene_id": "B1", "speaker_character_id": "CHAR_B", "embedding": np.asarray([0.0, 1.0], dtype=np.float32)},
            {"scene_id": "B2", "speaker_character_id": "CHAR_B", "embedding": np.asarray([0.20, 0.98], dtype=np.float32)},
        ]
        report = build_speaker_calibration(samples)
        self.assertEqual(report["status"], "calibrated")
        self.assertIsNone(report["threshold"])
        self.assertEqual(report["character_status"], "calibrated")
        self.assertIsNotNone(report["speaker_calibrations"]["CHAR_A"]["threshold"])
        self.assertIsNotNone(report["speaker_calibrations"]["CHAR_B"]["threshold"])
        self.assertEqual(report["speaker_calibrations"]["CHAR_A"]["status"], "calibrated")
        self.assertEqual(report["speaker_calibrations"]["CHAR_B"]["status"], "calibrated")

    def test_overlap_is_ambiguous(self):
        samples = [
            {"scene_id": "A1", "speaker_character_id": "CHAR_A", "embedding": np.asarray([1.0, 0.0], dtype=np.float32)},
            {"scene_id": "A2", "speaker_character_id": "CHAR_A", "embedding": np.asarray([0.60, 0.80], dtype=np.float32)},
            {"scene_id": "B1", "speaker_character_id": "CHAR_B", "embedding": np.asarray([0.80, 0.60], dtype=np.float32)},
            {"scene_id": "B2", "speaker_character_id": "CHAR_B", "embedding": np.asarray([0.0, 1.0], dtype=np.float32)},
        ]
        report = build_speaker_calibration(samples)
        self.assertEqual(report["status"], "ambiguous")
        self.assertIsNone(report["threshold"])


if __name__ == "__main__":
    unittest.main()
