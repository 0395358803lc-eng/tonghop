import unittest

from .app import _classify_error
from .browser import canonical_video_model_variants, classify_flow_generation_error_text, model_selection_variants, resolution_selection_state


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

    def test_resolution_fixed_default_is_not_false_mismatch(self):
        self.assertEqual(resolution_selection_state("720p", "", []), "fixed_default")

    def test_resolution_visible_in_summary_is_active(self):
        self.assertEqual(resolution_selection_state("720p", "Video 720p 8s", []), "active")

    def test_resolution_visible_other_value_is_mismatch(self):
        self.assertEqual(resolution_selection_state("720p", "", ["360p"]), "mismatch")

    def test_policy_error_tile_is_classified_immediately(self):
        text = (
            "Không thành công Câu lệnh này có thể vi phạm chính sách của chúng tôi "
            "về việc tạo video liên quan đến người nổi tiếng. Bạn chưa bị tính phí."
        )
        self.assertEqual(
            classify_flow_generation_error_text(text),
            ("FLOW_POLICY_BLOCKED", text),
        )
        self.assertEqual(
            _classify_error(RuntimeError(f"FLOW_POLICY_BLOCKED: {text}")),
            "FLOW_POLICY_BLOCKED",
        )

    def test_generic_error_tile_is_generation_failed(self):
        text = "Không thành công Vui lòng thử lại."
        self.assertEqual(
            classify_flow_generation_error_text(text),
            ("FLOW_GENERATION_FAILED", text),
        )
        self.assertEqual(
            _classify_error(RuntimeError(f"FLOW_GENERATION_FAILED: {text}")),
            "FLOW_GENERATION_FAILED",
        )

    def test_non_error_tile_text_is_ignored(self):
        self.assertIsNone(
            classify_flow_generation_error_text("Video · 720p · 8 giây")
        )


if __name__ == "__main__":
    unittest.main()
