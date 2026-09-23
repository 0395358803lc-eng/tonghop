import os
import unittest
from unittest.mock import patch

from . import desktop_network


class DesktopNetworkTests(unittest.TestCase):
    def test_forced_offline_and_online_status(self):
        with patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "offline"}):
            offline = desktop_network.internet_status(force=True)
        self.assertFalse(offline["online"])
        self.assertEqual(offline["message"], "Chưa kết nối Internet")

        with patch.dict(os.environ, {"TH_MEDIA_NETWORK_FORCE": "online"}):
            online = desktop_network.internet_status(force=True)
        self.assertTrue(online["online"])
        self.assertEqual(online["message"], "Đã kết nối Internet")

    def test_remote_routes_require_internet(self):
        remote = [
            ("POST", "/api/chats/chat-1/messages"),
            ("POST", "/api/flow/test"),
            ("GET", "/api/flow/projects"),
            ("POST", "/api/video/jobs"),
            ("POST", "/api/film/projects/p1/analyze"),
            ("POST", "/api/film/projects/p1/resources/generate"),
            ("POST", "/api/film/projects/p1/render/queue"),
            ("POST", "/api/film/projects/p1/pipeline/start"),
            ("POST", "/api/film/projects/p1/pipeline/resume"),
            ("POST", "/api/film/projects/p1/final/qc"),
        ]
        for method, path in remote:
            with self.subTest(method=method, path=path):
                self.assertTrue(desktop_network.requires_internet(method, path))

    def test_local_routes_remain_available_offline(self):
        local = [
            ("GET", "/api/desktop/network"),
            ("GET", "/api/film/projects"),
            ("GET", "/api/film/projects/p1"),
            ("GET", "/api/film/projects/p1/media"),
            ("GET", "/api/film/media/m1/file"),
            ("POST", "/api/film/projects"),
            ("POST", "/api/film/projects/p1/final/assemble"),
            ("GET", "/api/chats"),
            ("GET", "/api/video/jobs"),
        ]
        for method, path in local:
            with self.subTest(method=method, path=path):
                self.assertFalse(desktop_network.requires_internet(method, path))


if __name__ == "__main__":
    unittest.main()
