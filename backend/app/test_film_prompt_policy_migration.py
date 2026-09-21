import unittest
from unittest.mock import patch

from .film_prompt_policy_migration import (
    POLICY_VERSION,
    apply_prompt_policy_migration,
    build_prompt_policy_migration_plan,
)


class PromptPolicyMigrationTests(unittest.TestCase):
    def _project(self):
        return {
            "id": "P1",
            "visual_style": "Cinematic",
            "characters": [],
            "locations": [],
            "props": [],
            "scenes": [
                {
                    "id": "SCENE_001",
                    "scene_index": 0,
                    "source_hash": "h1",
                    "flow_prompt": "old-1",
                    "flow_prompt_meta": {"compiler": "v2", "prompt_hash": "old-h1"},
                    "characters": [],
                    "dialogue": [],
                    "voiceover": "",
                },
                {
                    "id": "SCENE_002",
                    "scene_index": 1,
                    "source_hash": "h2",
                    "flow_prompt": "old-2",
                    "flow_prompt_meta": {"compiler": "v2", "prompt_hash": "old-h2"},
                    "characters": [],
                    "dialogue": [],
                    "voiceover": "Narration",
                },
            ],
        }

    def test_plan_keeps_passed_media_and_stales_failed_policy(self):
        project = self._project()
        acceptance = {
            "items": [
                {
                    "scene_id": "SCENE_002",
                    "passed": False,
                    "stt_passed": True,
                    "status": "calibration_ambiguous",
                    "speaker_character_id": "NARRATOR",
                }
            ]
        }
        states = {
            "SCENE_001": {"status": "APPROVED", "selected_media_id": "m1"},
            "SCENE_002": {"status": "APPROVED", "selected_media_id": "m2"},
        }

        with patch(
            "app.film_prompt_policy_migration.get_film_project",
            return_value=project,
        ), patch(
            "app.film_prompt_policy_migration.verify_source_lock",
            return_value=(True, None),
        ), patch(
            "app.film_prompt_policy_migration.get_scene_state",
            side_effect=lambda _pid, sid: states[sid],
        ), patch(
            "app.film_prompt_policy_migration.validate_approval_for_snapshot",
            return_value=(True, None),
        ), patch(
            "app.film_prompt_policy_migration.compile_flow_prompt",
            side_effect=[
                ("new-1", {"compiler": POLICY_VERSION, "prompt_hash": "new-h1"}),
                ("new-2", {"compiler": POLICY_VERSION, "prompt_hash": "new-h2"}),
            ],
        ):
            plan = build_prompt_policy_migration_plan("P1", acceptance=acceptance)

        self.assertFalse(plan["blocked"])
        self.assertEqual(plan["keep_media_count"], 1)
        self.assertEqual(plan["stale_rerender_count"], 1)
        by_scene = {row["scene_id"]: row for row in plan["rows"]}
        self.assertEqual(by_scene["SCENE_001"]["action"], "KEEP_MEDIA_REBASE")
        self.assertEqual(by_scene["SCENE_002"]["action"], "STALE_RERENDER")
        self.assertIn("SPEAKER_POLICY_FAILED", by_scene["SCENE_002"]["reason"])

    def test_apply_rebases_pass_and_stales_only_failed_scene(self):
        project = self._project()
        plan = {
            "project_id": "P1",
            "policy": POLICY_VERSION,
            "blocked": False,
            "source_hashes": {"SCENE_001": "h1", "SCENE_002": "h2"},
            "rows": [
                {
                    "scene_id": "SCENE_001",
                    "action": "KEEP_MEDIA_REBASE",
                    "policy": {"passed": True, "policy": POLICY_VERSION},
                    "reason": None,
                },
                {
                    "scene_id": "SCENE_002",
                    "action": "STALE_RERENDER",
                    "policy": {"passed": False, "policy": POLICY_VERSION},
                    "reason": "SPEAKER_POLICY_FAILED:calibration_ambiguous",
                },
            ],
        }
        with patch(
            "app.film_prompt_policy_migration.pipeline_worker_active",
            return_value=False,
        ), patch(
            "app.film_prompt_policy_migration.build_prompt_policy_migration_plan",
            return_value=plan,
        ), patch(
            "app.film_prompt_policy_migration.auto_repair_project_derived",
            return_value=project,
        ), patch(
            "app.film_prompt_policy_migration.verify_source_lock",
            return_value=(True, None),
        ), patch(
            "app.film_prompt_policy_migration.run_production_gate",
            return_value={"final_gate": True},
        ), patch(
            "app.film_prompt_policy_migration.create_acceptance_snapshot",
            return_value={"id": "snap-1", "snapshot_hash": "sh1"},
        ) as create_snap, patch(
            "app.film_prompt_policy_migration.propagate_scene_change",
            return_value={"scene": {"status": "STALE"}},
        ) as stale_scene, patch(
            "app.film_prompt_policy_migration.snapshot_fingerprint_mismatch",
            return_value=False,
        ), patch(
            "app.film_prompt_policy_migration.append_repair_log",
        ) as append_log:
            result = apply_prompt_policy_migration("P1", acceptance={"items": []})

        create_snap.assert_called_once()
        self.assertEqual(create_snap.call_args.args[:2], ("P1", "SCENE_001"))
        stale_scene.assert_called_once()
        self.assertEqual(stale_scene.call_args.args[:2], ("P1", "SCENE_002"))
        append_log.assert_called_once()
        self.assertEqual([x["scene_id"] for x in result["rebased"]], ["SCENE_001"])
        self.assertEqual([x["scene_id"] for x in result["stale"]], ["SCENE_002"])
        self.assertEqual(result["source_mutations"], 0)

    def test_apply_blocks_when_pipeline_active(self):
        with patch(
            "app.film_prompt_policy_migration.pipeline_worker_active",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "PIPELINE_ACTIVE"):
                apply_prompt_policy_migration("P1", acceptance={"items": []})


if __name__ == "__main__":
    unittest.main()
