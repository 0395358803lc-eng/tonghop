import unittest

from .app import _classify_error
from .browser import canonical_video_model_variants, model_selection_variants


class VideoSelectorLogicTests(unittest.TestCase):
    def test_low_priority_never_falls_back_to_paid_base(self):
        model = "Veo 3.1 - Lite [Lower Priority]"
        self.assertEqual(model_selection_variants(model), [model])

    def test_regular_model_keeps_canonical_variant(self):
        self.assertEqual(
            canonical_video_model_variants("Veo 3.1 - Lite"),
            ["Veo 3.1 - Lite"],
        )

    def test_selector_model_error_is_ui_changed_not_capability(self):
        self.assertEqual(
            _classify_error(RuntimeError("Không tìm thấy selector model Flow.")),
            "FLOW_UI_CHANGED",
        )

    def test_model_selection_mismatch_is_ui_changed(self):
        self.assertEqual(
            _classify_error(
                RuntimeError(
                    "MODEL_SELECTION_MISMATCH: yêu cầu Veo 3.1 - Lite [Lower Priority] nhưng UI đang giữ Veo 3.1 - Lite"
                )
            ),
            "FLOW_UI_CHANGED",
        )

    def test_missing_model_option_is_capability_mismatch(self):
        self.assertEqual(
            _classify_error(RuntimeError("Không tìm thấy model Flow 'Veo X'.")),
            "CAPABILITY_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
