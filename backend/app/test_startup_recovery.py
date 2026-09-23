import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import film_recovery_service as recovery


class StartupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        tasks = list(recovery._RECOVERY_WORKERS)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        recovery._RECOVERY_WORKERS.clear()

    async def test_active_lease_is_deferred_without_duplicate_worker(self):
        runs = [{"id": "run-1", "project_id": "p1", "status": "running"}]
        worker = AsyncMock(return_value={})
        with (
            patch.object(recovery, "list_active_runs", return_value=runs),
            patch.object(recovery, "execution_lease_active", return_value=True),
            patch("app.film_pipeline_service.pipeline_worker_active", return_value=False),
            patch("app.film_pipeline_service.process_pipeline", worker),
        ):
            result = await recovery.recover_interrupted_pipelines_once()
        self.assertEqual(result["scheduled"], [])
        self.assertEqual(result["deferred"][0]["reason"], "active_worker_or_lease")
        worker.assert_not_awaited()

    async def test_expired_lease_schedules_recovery_worker(self):
        runs = [{"id": "run-2", "project_id": "p2", "status": "running"}]
        worker = AsyncMock(return_value={"project_id": "p2"})
        with (
            patch.object(recovery, "list_active_runs", return_value=runs),
            patch.object(recovery, "execution_lease_active", return_value=False),
            patch.object(recovery, "reconcile_project", return_value={"project_id": "p2"}),
            patch.object(recovery, "emit_event"),
            patch("app.film_pipeline_service.pipeline_worker_active", return_value=False),
            patch("app.film_pipeline_service.process_pipeline", worker),
        ):
            result = await recovery.recover_interrupted_pipelines_once()
            await asyncio.sleep(0)

        self.assertEqual(result["scheduled"], ["p2"])
        worker.assert_awaited_once_with("p2")


if __name__ == "__main__":
    unittest.main()
