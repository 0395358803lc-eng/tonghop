import unittest
from unittest.mock import patch

from .film_prompt_policy_migration import (
    POLICY_VERSION,
    apply_prompt_policy_migration,
    build_prompt_policy_migration_plan,
    rebase_prompt_policy_snapshots,
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
                    "policy_accepted": False,
                    "verification_state": "blocked",
                    "stt_passed": True,
                    "status": "failed",
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


    def test_plan_keeps_unverified_ambiguous_speaker_with_warning(self):
        project = self._project()
        acceptance = {
            "items": [
                {
                    "scene_id": "SCENE_002",
                    "passed": False,
                    "policy_accepted": True,
                    "verification_state": "unverified",
                    "stt_passed": True,
                    "status": "calibration_ambiguous",
                    "speaker_character_id": "NARRATOR",
                    "raw_similarity": 0.149349,
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

        self.assertEqual(plan["keep_media_count"], 2)
        self.assertEqual(plan["stale_rerender_count"], 0)
        by_scene = {row["scene_id"]: row for row in plan["rows"]}
        row = by_scene["SCENE_002"]
        self.assertEqual(row["action"], "KEEP_MEDIA_REBASE")
        self.assertTrue(row["policy"]["passed"])
        self.assertFalse(row["policy"]["strict_verified"])
        self.assertEqual(row["policy"]["verification_state"], "unverified")
        self.assertIn("SPEAKER_IDENTITY_UNVERIFIED", row["policy"]["warning"])
        self.assertIsNone(row["reason"])

    def test_plan_recovers_only_snapshot_fingerprint_stale(self):
        project = self._project()
        project["scenes"][0]["voiceover"] = ""
        project["scenes"][1]["voiceover"] = ""
        states = {
            "SCENE_001": {
                "status": "STALE",
                "selected_media_id": "m1",
                "blocked_reason": "SNAPSHOT_FINGERPRINT_MISMATCH",
                "error": "SNAPSHOT_FINGERPRINT_MISMATCH",
            },
            "SCENE_002": {
                "status": "STALE",
                "selected_media_id": "m2",
                "blocked_reason": "PROMPT_POLICY_STALE:other",
                "error": "PROMPT_POLICY_STALE:other",
            },
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
        ) as validate, patch(
            "app.film_prompt_policy_migration.compile_flow_prompt",
            side_effect=[
                ("new-1", {"compiler": POLICY_VERSION, "prompt_hash": "new-h1"}),
                ("new-2", {"compiler": POLICY_VERSION, "prompt_hash": "new-h2"}),
            ],
        ):
            plan = build_prompt_policy_migration_plan("P1", acceptance={"items": []})

        by_scene = {row["scene_id"]: row for row in plan["rows"]}
        self.assertEqual(by_scene["SCENE_001"]["action"], "KEEP_MEDIA_REBASE")
        self.assertTrue(by_scene["SCENE_001"]["recoverable_snapshot_stale"])
        self.assertEqual(by_scene["SCENE_002"]["action"], "STALE_RERENDER")
        self.assertFalse(by_scene["SCENE_002"]["recoverable_snapshot_stale"])
        validate.assert_called_once()
        self.assertEqual(validate.call_args.kwargs["allowed_statuses"], {"STALE"})

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

    def test_rebase_snapshots_keeps_passed_media_and_preserves_existing_stale(self):
        plan = {
            "project_id": "P1",
            "policy": POLICY_VERSION,
            "blocked": False,
            "rows": [
                {
                    "scene_id": "SCENE_001",
                    "scene_index": 0,
                    "action": "KEEP_MEDIA_REBASE",
                    "recoverable_snapshot_stale": True,
                    "policy": {"passed": True, "policy": POLICY_VERSION},
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
            "app.film_prompt_policy_migration.create_acceptance_snapshot",
            return_value={"id": "snap-new", "snapshot_hash": "hash-new"},
        ) as create_snap, patch(
            "app.film_prompt_policy_migration.snapshot_fingerprint_mismatch",
            return_value=False,
        ), patch(
            "app.film_prompt_policy_migration.get_scene_state",
            return_value={"status": "STALE"},
        ), patch(
            "app.film_prompt_policy_migration.upsert_scene_state",
        ) as restore_state, patch(
            "app.film_prompt_policy_migration.propagate_scene_change",
        ) as propagate, patch(
            "app.film_prompt_policy_migration.append_repair_log",
        ) as append_log:
            result = rebase_prompt_policy_snapshots("P1", acceptance={"items": []})

        create_snap.assert_called_once()
        restore_state.assert_called_once()
        self.assertEqual(restore_state.call_args.kwargs["status"], "APPROVED")
        self.assertIsNone(restore_state.call_args.kwargs["blocked_reason"])
        self.assertIsNone(restore_state.call_args.kwargs["error"])
        propagate.assert_not_called()
        append_log.assert_called_once()
        self.assertEqual([x["scene_id"] for x in result["rebased"]], ["SCENE_001"])
        self.assertTrue(result["rebased"][0]["restored_approved"])
        self.assertEqual([x["scene_id"] for x in result["stale"]], ["SCENE_002"])
        self.assertTrue(result["stale"][0]["result"]["already_stale"])

    def test_apply_blocks_when_pipeline_active(self):
        with patch(
            "app.film_prompt_policy_migration.pipeline_worker_active",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "PIPELINE_ACTIVE"):
                apply_prompt_policy_migration("P1", acceptance={"items": []})


if __name__ == "__main__":
    unittest.main()
