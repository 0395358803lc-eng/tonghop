import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import sessions


class FlowSessionRuntimeTests(unittest.TestCase):
    def test_cdp_port_comes_from_runtime_config(self):
        with patch.object(
            sessions,
            "load_config",
            return_value={"cdp_url": "http://127.0.0.1:19321"},
        ):
            self.assertEqual(sessions.cdp_port(), 19321)

    def test_browser_falls_back_to_microsoft_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            program_files = Path(tmp) / "ProgramFiles"
            edge = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            edge.parent.mkdir(parents=True)
            edge.write_bytes(b"fake-edge")
            env = {
                "TH_MEDIA_CHROME_PATH": "",
                "TH_MEDIA_BROWSER_PATH": "",
                "ProgramFiles": str(program_files),
                "ProgramFiles(x86)": str(Path(tmp) / "ProgramFilesX86"),
                "LOCALAPPDATA": str(Path(tmp) / "LocalAppData"),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(sessions._chrome_exe(), edge)

    def test_start_flow_chrome_uses_runtime_cdp_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            with (
                patch.object(sessions, "ACTIVE_PROFILE", profile),
                patch.object(
                    sessions,
                    "load_config",
                    return_value={"cdp_url": "http://127.0.0.1:19321"},
                ),
                patch.object(sessions, "_chrome_exe", return_value=Path("chrome.exe")),
                patch.object(sessions, "port_open", return_value=False),
                patch.object(sessions, "wait_port", return_value=True) as wait_port,
                patch.object(sessions, "set_flow_chrome_visibility"),
                patch.object(sessions.time, "sleep"),
                patch.object(sessions.subprocess, "Popen") as popen,
            ):
                sessions.start_flow_chrome("https://flow.google.com/", visible=False)

            args = popen.call_args.args[0]
            self.assertIn("--remote-debugging-port=19321", args)
            wait_port.assert_called_once_with(19321, 20, want_open=True)

    def test_stop_flow_chrome_waits_for_runtime_port(self):
        with (
            patch.object(
                sessions,
                "load_config",
                return_value={"cdp_url": "http://127.0.0.1:19321"},
            ),
            patch.object(sessions, "chrome_pids", return_value=[]),
            patch.object(sessions, "wait_port", return_value=True) as wait_port,
        ):
            sessions.stop_flow_chrome()

        wait_port.assert_called_once_with(19321, 12, want_open=False)


if __name__ == "__main__":
    unittest.main()
