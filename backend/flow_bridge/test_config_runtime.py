import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import config


class FlowConfigRuntimeTests(unittest.TestCase):
    def test_ensure_config_round_trip_and_env_override(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path = Path(temp) / "flow_bridge_config.json"
            with patch.object(config, "CONFIG_PATH", cfg_path):
                written = config.ensure_config(
                    api_key="stored-key",
                    cdp_url="http://127.0.0.1:19321",
                    flow_url="https://flow.google.com/project/test",
                )
                self.assertEqual(written["api_key"], "stored-key")
                self.assertTrue(cfg_path.exists())
                disk = json.loads(cfg_path.read_text(encoding="utf-8"))
                self.assertEqual(disk["cdp_url"], "http://127.0.0.1:19321")

                with patch.dict(
                    os.environ,
                    {
                        "FLOW_BRIDGE_API_KEY": "runtime-key",
                        "FLOW_CDP_URL": "http://127.0.0.1:19444",
                    },
                    clear=False,
                ):
                    loaded = config.load_config()
                self.assertEqual(loaded["api_key"], "runtime-key")
                self.assertEqual(loaded["cdp_url"], "http://127.0.0.1:19444")
                self.assertEqual(loaded["flow_url"], "https://flow.google.com/project/test")

    def test_project_media_root_is_project_scoped_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = config.project_media_root('project-a', root)
            second = config.project_media_root('project-b', root)
            self.assertEqual(first, root.resolve() / 'projects' / 'project-a')
            self.assertNotEqual(first, second)
            with self.assertRaises(ValueError):
                config.project_media_root('../escape', root)
            with self.assertRaises(ValueError):
                config.project_media_root('project/a', root)

    def test_ensure_config_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path = Path(temp) / "flow_bridge_config.json"
            cfg_path.write_text(
                json.dumps({
                    "api_key": "old-key",
                    "cdp_url": "http://127.0.0.1:9223",
                    "flow_url": "https://flow.google.com/",
                }),
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_PATH", cfg_path):
                result = config.ensure_config(api_key="new-key")
            self.assertEqual(result["api_key"], "new-key")
            self.assertEqual(result["cdp_url"], "http://127.0.0.1:9223")
            self.assertEqual(result["flow_url"], "https://flow.google.com/")


if __name__ == "__main__":
    unittest.main()
