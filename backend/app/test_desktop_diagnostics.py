import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from . import desktop_diagnostics as diagnostics


class DesktopDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_redacts_secrets_and_excludes_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "Logs"
            output_dir = root / "Diagnostics"
            logs.mkdir(parents=True)
            secret = "secret-value-123"
            log = logs / "backend.log"
            log.write_text(
                f"Authorization: Bearer {secret}\n"
                f"FLOW_BRIDGE_API_KEY={secret}\n"
                f"api_key={secret}\n",
                encoding="utf-8",
            )
            with (
                patch.object(diagnostics, "DATA_DIR", root),
                patch.object(diagnostics, "_DIAGNOSTICS_DIR", output_dir),
                patch.object(diagnostics, "diagnostics_status", AsyncMock(return_value={"ok": True})),
                patch.object(diagnostics, "_safe_log_files", return_value=[log]),
            ):
                bundle = await diagnostics.export_diagnostics_bundle()

            self.assertTrue(bundle.is_file())
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn("diagnostics.json", names)
                self.assertIn("logs/backend.log", names)
                self.assertFalse(any(name.endswith(".db") for name in names))
                payload = archive.read("logs/backend.log").decode("utf-8")
                self.assertNotIn(secret, payload)
                self.assertIn("[REDACTED]", payload)

    def test_redactor_covers_desktop_token(self):
        source = "X-TH-Media-Token: token-abc"
        redacted = diagnostics._redact_log(source)
        self.assertNotIn("token-abc", redacted)
        self.assertIn("[REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main()
