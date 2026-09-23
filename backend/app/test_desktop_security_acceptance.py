import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class DesktopSecurityAcceptanceTests(unittest.TestCase):
    def test_desktop_process_rejects_missing_token_and_external_cors_origin(self):
        backend = Path(__file__).resolve().parents[1]
        code = textwrap.dedent(
            """
            import json
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app) as client:
                missing = client.get("/api/desktop/network")
                wrong = client.get("/api/desktop/network", headers={"X-TH-Media-Token": "wrong"})
                valid = client.get("/api/desktop/network", headers={"X-TH-Media-Token": "acceptance-token"})

                evil = client.options(
                    "/api/desktop/network",
                    headers={
                        "Origin": "https://evil.example",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "X-TH-Media-Token",
                    },
                )
                tauri = client.options(
                    "/api/desktop/network",
                    headers={
                        "Origin": "http://tauri.localhost",
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "X-TH-Media-Token",
                    },
                )
                print(json.dumps({
                    "missing": missing.status_code,
                    "wrong": wrong.status_code,
                    "valid": valid.status_code,
                    "evil_allow_origin": evil.headers.get("access-control-allow-origin"),
                    "tauri_allow_origin": tauri.headers.get("access-control-allow-origin"),
                }))
            """
        )
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data"
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONPATH": str(backend),
                    "TH_MEDIA_DESKTOP_MODE": "1",
                    "TH_MEDIA_AUTH_TOKEN": "acceptance-token",
                    "TH_MEDIA_DATA_DIR": str(data),
                    "TH_MEDIA_DB_PATH": str(data / "Database" / "aihub.db"),
                    "TH_MEDIA_KEY_PATH": str(data / "Database" / "master.key"),
                }
            )
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=backend,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["missing"], 401)
        self.assertEqual(result["wrong"], 401)
        self.assertEqual(result["valid"], 200)
        self.assertIsNone(result["evil_allow_origin"])
        self.assertEqual(result["tauri_allow_origin"], "http://tauri.localhost")


if __name__ == "__main__":
    unittest.main()
