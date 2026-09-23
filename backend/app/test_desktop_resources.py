import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from . import desktop_resources
from . import film_media_store


class DesktopResourceTests(unittest.TestCase):
    def test_legacy_media_root_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            current = base / "new-media"
            legacy = base / "old-media"
            current.mkdir()
            legacy.mkdir()
            old_file = legacy / "final.mp4"
            old_file.write_bytes(b"old-film")

            with (
                patch.object(film_media_store, "MEDIA_DIR", current),
                patch.object(film_media_store, "LEGACY_MEDIA_DIRS", (legacy,)),
            ):
                resolved = film_media_store.validate_media_path(old_file)
            self.assertEqual(resolved, old_file.resolve())
    def test_temp_cleanup_never_touches_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            temp_dir = base / "Temp"
            media_dir = base / "Media"
            temp_dir.mkdir()
            media_dir.mkdir()
            temp_file = temp_dir / "stale.bin"
            media_file = media_dir / "selected-final.mp4"
            temp_file.write_bytes(b"x" * 700)
            media_file.write_bytes(b"y" * 700)
            old = time.time() - 3600
            os.utime(temp_file, (old, old))

            with (
                patch.object(desktop_resources, "TEMP_DIR", temp_dir),
                patch.object(desktop_resources, "GIB", 1024),
            ):
                result = desktop_resources.cleanup_temp(quota_gb=0.5, min_age_seconds=0)

            self.assertGreaterEqual(result["deleted_files"], 1)
            self.assertFalse(temp_file.exists())
            self.assertTrue(media_file.exists())

    def test_resource_gate_blocks_only_critical_state(self):
        with (
            patch.object(desktop_resources, "cleanup_temp", return_value={"ok": True}),
            patch.object(
                desktop_resources,
                "resource_status",
                return_value={"can_render": False, "blockers": ["DISK_CRITICAL:1GB"], "warnings": []},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "DESKTOP_RESOURCE_BLOCKED"):
                desktop_resources.assert_render_resources()


if __name__ == "__main__":
    unittest.main()
