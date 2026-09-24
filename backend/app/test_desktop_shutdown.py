import unittest

from .data_isolation import IsolatedDataMixin, IsolatedDataTestCase
from .db import init_db
from .desktop_shutdown import (
    prepare_force_shutdown,
    request_safe_shutdown,
    shutdown_status,
)
from .film_scene_state_store import (
    create_run,
    get_run,
    update_run,
)
from .film_store import create_film_project, delete_film_project


class DesktopShutdownTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        init_db()

    def setUp(self):
        self.project = create_film_project(
            "__desktop_shutdown_test__",
            "Kịch bản kiểm thử shutdown desktop an toàn dài hơn hai mươi ký tự.",
            "xkiro",
            "test-model",
            {},
        )

    def tearDown(self):
        delete_film_project(self.project["id"])

    def test_safe_shutdown_marks_running_pipeline_stop_after_current(self):
        run = create_run(self.project["id"])
        status = shutdown_status()
        self.assertFalse(status["safe_to_exit"])
        self.assertEqual(status["active_count"], 1)

        result = request_safe_shutdown()
        updated = get_run(run["id"])
        self.assertEqual(updated["status"], "stopping")
        self.assertTrue(updated["stop_after_current"])
        self.assertEqual(result["requested"], "stop_after_current_scene")

    def test_safe_shutdown_stops_paused_pipeline_immediately(self):
        run = create_run(self.project["id"])
        update_run(run["id"], status="paused")

        request_safe_shutdown()

        updated = get_run(run["id"])
        self.assertEqual(updated["status"], "stopped")
        self.assertTrue(shutdown_status()["safe_to_exit"])

    def test_force_shutdown_checkpoints_running_pipeline_as_paused(self):
        run = create_run(self.project["id"])
        update_run(run["id"], current_scene_id="SCENE_007", current_scene_index=6)

        result = prepare_force_shutdown()

        updated = get_run(run["id"])
        self.assertEqual(updated["status"], "paused")
        self.assertFalse(updated["stop_after_current"])
        self.assertIn(run["id"], result["checkpointed_run_ids"])
        actions = [item.get("action") for item in (updated.get("log") or [])]
        self.assertIn("desktop_force_exit_checkpoint", actions)


if __name__ == "__main__":
    unittest.main()
