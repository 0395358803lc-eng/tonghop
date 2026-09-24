import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .data_isolation import IsolatedDataTestCase
from . import api


class DesktopReadyStatusTests(IsolatedDataTestCase):
    def test_ready_includes_desktop_runtime_and_storage_summary(self):
        with (
            patch.dict(os.environ, {"TH_MEDIA_DESKTOP_MODE": "1", "TH_MEDIA_FLOW_BRIDGE_PORT": "8765"}, clear=False),
            patch.object(api.socket, "create_connection"),
            patch.object(api, "event_store_available", return_value=True),
            patch.object(api, "get_flow_status", return_value={"configured": True}),
            patch.object(
                api,
                "test_flow_bridge",
                new=AsyncMock(return_value={
                    "ok": True,
                    "authenticated": True,
                    "session": {"authenticated": True, "state": "AUTHENTICATED"},
                }),
            ),
            patch.object(
                api,
                "speaker_identity_status",
                return_value={"enabled": True, "required": True, "model_exists": True},
            ),
            patch.object(api, "matrix_is_fresh", return_value=True),
            patch.object(api, "list_saved", return_value={"xkiro": {"masked_key": "••••"}}),
            patch.object(
                api,
                "list_active_runs",
                return_value=[{"id": "run-1"}, {"id": "run-2"}],
            ),
            patch.object(
                api.shutil,
                "disk_usage",
                return_value=SimpleNamespace(
                    total=100 * 1024**3,
                    used=65 * 1024**3,
                    free=35 * 1024**3,
                ),
            ),
        ):
            payload = asyncio.run(api.ready())

        self.assertTrue(payload["health"])
        self.assertTrue(payload["runtime"]["desktop_mode"])
        self.assertEqual(payload["runtime"]["active_pipelines"], 2)
        self.assertEqual(payload["runtime"]["configured_provider_count"], 1)
        self.assertEqual(payload["runtime"]["configured_providers"], ["xkiro"])
        self.assertEqual(payload["storage"]["free_gb"], 35.0)
        self.assertEqual(payload["storage"]["used_percent"], 65.0)
        self.assertTrue(payload["checks"]["flow_authenticated"])
        self.assertTrue(payload["checks"]["speaker_verifier_ready"])


if __name__ == "__main__":
    unittest.main()
