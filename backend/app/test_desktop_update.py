import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import desktop_update


class DesktopUpdateTests(unittest.TestCase):
    def test_update_gate_blocks_active_pipeline(self):
        with patch.object(desktop_update, "list_active_runs", return_value=[
            {"project_id": "P1", "run_id": "R1", "status": "running", "current_scene_id": "SCENE_002"}
        ]):
            status = desktop_update.update_gate_status()
        self.assertFalse(status["can_update"])
        self.assertEqual(status["active_pipelines"], 1)

    def test_prepare_update_creates_sqlite_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "Database" / "aihub.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO sample(value) VALUES ('preserved')")
                conn.commit()
            finally:
                conn.close()
            backups = root / "Backups"
            with (
                patch.object(desktop_update, "DB_PATH", db),
                patch.object(desktop_update, "BACKUP_ROOT", backups),
                patch.object(desktop_update, "list_active_runs", return_value=[]),
            ):
                result = desktop_update.prepare_update_backup()

            self.assertTrue(result["prepared"])
            backup_db = Path(result["database_backup"])
            self.assertTrue(backup_db.is_file())
            conn = sqlite3.connect(backup_db)
            try:
                value = conn.execute("SELECT value FROM sample").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(value, "preserved")
            self.assertTrue((backup_db.parent / "manifest.json").is_file())


    def test_user_backup_includes_settings_and_can_stage_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "Database" / "aihub.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO sample(value) VALUES ('restore-me')")
                conn.commit()
            finally:
                conn.close()
            (root / "desktop-settings.json").write_text('{"minimize_to_tray": false}', encoding="utf-8")
            backups = root / "Backups"

            with (
                patch.object(desktop_update, "DATA_DIR", root),
                patch.object(desktop_update, "DB_PATH", db),
                patch.object(desktop_update, "BACKUP_ROOT", backups),
                patch.object(desktop_update, "list_active_runs", return_value=[]),
            ):
                created = desktop_update.create_desktop_backup("user")
                listed = desktop_update.list_desktop_backups()
                staged = desktop_update.stage_desktop_restore(created["backup_name"])

            self.assertTrue(created["prepared"])
            self.assertTrue((Path(created["backup_dir"]) / "desktop-settings.json").is_file())
            self.assertEqual(listed["backups"][0]["name"], created["backup_name"])
            self.assertTrue(staged["staged"])
            self.assertTrue((root / "pending-restore.json").is_file())

    def test_stage_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backups = root / "Backups"
            backups.mkdir()
            db = root / "Database" / "aihub.db"
            db.parent.mkdir(parents=True)
            sqlite3.connect(db).close()
            with (
                patch.object(desktop_update, "DATA_DIR", root),
                patch.object(desktop_update, "DB_PATH", db),
                patch.object(desktop_update, "BACKUP_ROOT", backups),
                patch.object(desktop_update, "list_active_runs", return_value=[]),
            ):
                with self.assertRaisesRegex(ValueError, "BACKUP_NAME_INVALID"):
                    desktop_update.stage_desktop_restore("../escape")

    def test_user_backup_retention_prunes_only_user_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backups = root / "Backups"
            backups.mkdir()
            (root / "desktop-settings.json").write_text('{"backup_keep": 2}', encoding="utf-8")
            for name in ("user-20260101T000000Z", "user-20260102T000000Z", "user-20260103T000000Z", "pre-update-20260101T000000Z"):
                (backups / name).mkdir()
            with (
                patch.object(desktop_update, "DATA_DIR", root),
                patch.object(desktop_update, "BACKUP_ROOT", backups),
            ):
                desktop_update._prune_user_backups()
            self.assertFalse((backups / "user-20260101T000000Z").exists())
            self.assertTrue((backups / "user-20260102T000000Z").exists())
            self.assertTrue((backups / "user-20260103T000000Z").exists())
            self.assertTrue((backups / "pre-update-20260101T000000Z").exists())


if __name__ == "__main__":
    unittest.main()
