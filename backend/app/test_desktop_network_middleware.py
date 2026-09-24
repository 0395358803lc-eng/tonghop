import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from .data_isolation import IsolatedDataTestCase
from .main import app


class DesktopNetworkMiddlewareTests(IsolatedDataTestCase):
    def test_offline_keeps_local_api_but_blocks_remote_api(self):
        with patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "offline"}):
            with TestClient(app) as client:
                network = client.get("/api/desktop/network")
                self.assertEqual(network.status_code, 200)
                self.assertFalse(network.json()["online"])

                projects = client.get("/api/film/projects")
                self.assertEqual(projects.status_code, 200)

                remote = client.post(
                    "/api/chats/nonexistent/messages",
                    json={"content": "hello"},
                )
                self.assertEqual(remote.status_code, 503)
                self.assertEqual(remote.json().get("code"), "INTERNET_OFFLINE")
                self.assertIn("Chưa kết nối Internet", remote.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
