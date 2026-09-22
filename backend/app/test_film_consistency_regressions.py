import unittest

from .film_consistency_v2 import _canonical_scene_id, _scene_results, _verify_semantic_issues
from .film_integrity import _derive_action_state_dimensions, structure_state


class FilmConsistencyRegressionTests(unittest.TestCase):
    def test_negated_prop_reference_is_not_presence(self):
        state = structure_state(
            "CHAR_001 exiting to LOC_001; light mist; no PROP_001 (left inside as open box remains on bench).",
            declared_characters=["CHAR_001"],
            location_id="LOC_001",
            props_present=[],
        )
        self.assertNotIn("PROP_001", state["entities"])
        self.assertNotIn("PROP_001", state["props"])
        self.assertIn("LOC_001", state["entities"])

    def test_vietnamese_negated_prop_reference_is_not_presence(self):
        state = structure_state(
            "CHAR_001 ở ngoài phố; không có PROP_001 trong cảnh này.",
            declared_characters=["CHAR_001"],
            location_id="LOC_001",
            props_present=[],
        )
        self.assertNotIn("PROP_001", state["entities"])
        self.assertNotIn("PROP_001", state["props"])

    def test_declared_prop_still_wins_over_textual_negation_filter(self):
        state = structure_state(
            "No PROP_001 is visible in the close-up text note.",
            declared_characters=[],
            location_id="LOC_001",
            props_present=["PROP_001"],
        )
        self.assertIn("PROP_001", state["entities"])
        self.assertIn("PROP_001", state["props"])

    def test_ai_scene_id_is_normalized_to_project_id(self):
        project = {"scenes": [{"id": "SCENE_014"}, {"id": "SCENE_015"}]}
        self.assertEqual(_canonical_scene_id("SCENE_14", project), "SCENE_014")
        self.assertEqual(_canonical_scene_id("scene_015", project), "SCENE_015")
        self.assertEqual(_canonical_scene_id("PROJECT", project), "PROJECT")

    def test_location_false_positive_from_negated_prop_is_rejected(self):
        project = {
            "scenes": [{
                "id": "SCENE_014",
                "location_id": "LOC_001",
                "props_present": [],
                "start_state": "CHAR_001 exiting to LOC_001; no PROP_001 (left inside as open box remains on bench).",
                "end_state": "CHAR_001 mid-street.",
                "action": "An walks outside.",
                "source_text": "",
            }]
        }
        semantic = {
            "available": True,
            "verdict": "FAIL",
            "issues": [{
                "scene_id": "SCENE_014",
                "issue_type": "LOCATION",
                "severity": "hard",
                "deterministic_code": "PROP_LOCATION_MISMATCH",
                "disposition": "NEW_ISSUE",
                "entity_id": "PROP_001",
                "dimension": None,
                "actual_value": "LOC_002",
                "expected_value": None,
                "confidence": 0.95,
                "repairability": "SAFE_DERIVED",
                "suggested_patch_type": "REBUILD_STRUCTURED_STATE",
            }],
        }
        verified = _verify_semantic_issues(semantic, {"prop_ledger": [], "final_gate": True}, project)
        self.assertEqual(verified["issues"], [])
        self.assertEqual(len(verified["rejected_findings"]), 1)
        self.assertFalse(verified["rejected_findings"][0]["evidence_verified"])
        self.assertEqual(verified["verdict"], "PASS")
        self.assertTrue(verified["verdict_normalized_by_evidence_verifier"])

    def test_watch_close_and_ticking_are_derived_from_source_action(self):
        closed = _derive_action_state_dimensions(
            {
                "action": "Ông chạm góc giấy rồi đóng nắp, bắt đầu lên dây cót.",
                "start_state": "PROP_002 case back open on tray.",
                "end_state": "Case back closed; crown being wound.",
            },
            {"visibility_state": "revealed", "mechanical_state": "open"},
        )
        self.assertEqual(closed.get("mechanical_state"), "closed")

        ticking = _derive_action_state_dimensions(
            {
                "action": "Kim giây giật một nhịp rồi chạy. Tiếng tick nhỏ.",
                "start_state": "Watch wound.",
                "end_state": "Watch ticking.",
            },
            {"visibility_state": "revealed", "mechanical_state": "closed"},
        )
        self.assertEqual(ticking.get("rotation_state"), "spinning")

    def test_boundary_location_guess_is_rejected(self):
        project = {
            "scenes": [{
                "id": "SCENE_003",
                "location_id": "LOC_001",
                "props_present": [],
                "action": "Hai người đứng ở ngưỡng cửa.",
                "start_state": "CHAR_001 entering from LOC_001; CHAR_002 at bench in LOC_002.",
                "end_state": "Both at LOC_001↔LOC_002 junction.",
                "source_text": "",
                "source_snapshot": {"location_id": "LOC_001"},
            }]
        }
        semantic = {
            "available": True,
            "verdict": "FAIL",
            "issues": [{
                "scene_id": "SCENE_003",
                "issue_type": "LOCATION",
                "severity": "hard",
                "deterministic_code": "LOC_MISMATCH_THRESHOLD",
                "disposition": "CONFIRMED_ERROR",
                "entity_id": "LOC_001",
                "dimension": None,
                "actual_value": "LOC_001",
                "expected_value": "LOC_002",
                "confidence": 0.95,
                "repairability": "SEMANTIC_NORMALIZATION",
                "suggested_patch_type": "REBUILD_STRUCTURED_STATE",
            }],
        }
        verified = _verify_semantic_issues(semantic, {"prop_ledger": [], "final_gate": True}, project)
        self.assertEqual(verified["issues"], [])
        self.assertEqual(verified["verdict"], "PASS")
        self.assertIn("boundary/junction", verified["rejected_findings"][0]["verification_note"])

    def test_scene_results_maps_review_scene_id(self):
        result = _scene_results(
            {"scenes": [{"id": "SCENE_003"}, {"id": "SCENE_004"}]},
            {
                "effective_deterministic_errors": [],
                "semantic_errors": [],
                "review_items": [{"scene_id": "SCENE_003", "repairability": "NONE"}],
            },
        )
        by_id = {item["scene_id"]: item for item in result}
        self.assertFalse(by_id["SCENE_003"]["passed"])
        self.assertTrue(by_id["SCENE_004"]["passed"])

    def test_explicit_source_state_beats_ai_aesthetic_guess(self):
        project = {
            "scenes": [{
                "id": "SCENE_015",
                "action": "Ông Quang ngồi cạnh cửa sổ.",
                "start_state": "CHAR_002 at bench; opened PROP_001 on bench.",
                "end_state": "Same positions; watch still ticking.",
                "source_text": "",
            }]
        }
        deterministic = {
            "final_gate": True,
            "prop_ledger": [{
                "scene_id": "SCENE_015",
                "prop_id": "PROP_001",
                "owner": "CHAR_002",
                "container": None,
                "state": "open",
                "state_dimensions": {"mechanical_state": "open"},
                "relations": {},
            }],
        }
        semantic = {
            "available": True,
            "verdict": "FAIL",
            "issues": [{
                "scene_id": "SCENE_015",
                "issue_type": "PROP_STATE",
                "severity": "medium",
                "deterministic_code": "CONTINUITY_LOGIC_GAP",
                "disposition": "CONFIRMED_ERROR",
                "entity_id": "PROP_001",
                "dimension": "mechanical_state",
                "actual_value": "open",
                "expected_value": "closed",
                "confidence": 0.85,
                "repairability": "SOURCE_SENSITIVE",
                "suggested_patch_type": "RECOMPILE_PROMPT",
            }],
        }
        verified = _verify_semantic_issues(semantic, deterministic, project)
        self.assertEqual(verified["issues"], [])
        self.assertEqual(verified["verdict"], "PASS")
        self.assertIn("Source", verified["rejected_findings"][0]["verification_note"])

    def test_owner_guess_cannot_override_explicit_transfer_ledger(self):
        project = {
            "props": [{"id": "PROP_001", "owner_initial": "CHAR_001"}],
            "scenes": [
                {
                    "id": "SCENE_005",
                    "prop_transfers": [{
                        "prop_id": "PROP_001",
                        "source_owner": "CHAR_001",
                        "target_owner": "CHAR_002",
                    }],
                },
                {
                    "id": "SCENE_015",
                    "prop_transfers": [],
                },
            ],
        }
        deterministic = {
            "final_gate": True,
            "prop_ledger": [{
                "scene_id": "SCENE_015",
                "prop_id": "PROP_001",
                "owner": "CHAR_002",
                "container": None,
                "state": None,
                "state_dimensions": {},
                "relations": {},
            }],
        }
        semantic = {
            "available": True,
            "verdict": "FAIL",
            "issues": [{
                "scene_id": "SCENE_015",
                "issue_type": "PROP_STATE",
                "severity": "hard",
                "deterministic_code": "OWNER_GUESS",
                "disposition": "CONFIRMED_ERROR",
                "entity_id": "PROP_001",
                "dimension": "owner",
                "actual_value": "CHAR_002",
                "expected_value": "CHAR_001",
                "confidence": 0.9,
                "repairability": "SAFE_DERIVED",
                "suggested_patch_type": "REBUILD_STRUCTURED_STATE",
            }],
        }
        verified = _verify_semantic_issues(semantic, deterministic, project)
        self.assertEqual(verified["issues"], [])
        self.assertEqual(len(verified["rejected_findings"]), 1)
        self.assertIn("PROP_TRANSFERS", verified["rejected_findings"][0]["verification_note"])
        self.assertEqual(verified["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
