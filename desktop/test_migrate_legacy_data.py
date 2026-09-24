import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from migrate_legacy_data import default_target, migrate, sqlite_integrity


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LegacyMigrationTests(unittest.TestCase):
    def test_default_target_matches_desktop_runtime_root(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=False):
                self.assertEqual(default_target(), Path(temp) / "TH Media" / "Desktop")

    def make_fixture(self, root: Path) -> Path:
        source = root / ".data"
        source.mkdir()
        (source / "master.key").write_bytes(b"test-master-key")
        (source / "generated_media").mkdir()
        (source / "generated_media" / "clip.bin").write_bytes(b"media-payload")
        (source / "final_films").mkdir()
        (source / "final_films" / "final.mp4").write_bytes(b"final-payload")

        profile = source / "flow_chrome_profile" / "Default"
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text('{"ok":true}', encoding="utf-8")
        (source / "flow_chrome_profile" / "lockfile").write_text("skip", encoding="utf-8")
        session = source / "flow_sessions" / "session-a"
        session.mkdir(parents=True)
        (session / "Preferences").write_text("session", encoding="utf-8")
        legacy_media = str(source / "generated_media" / "clip.bin")
        legacy_profile = str(source / "flow_chrome_profile")
        db = sqlite3.connect(source / "aihub.db")
        db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, file_path TEXT, metadata_json TEXT)")
        db.execute(
            "INSERT INTO items(file_path,metadata_json) VALUES(?,?)",
            (legacy_media, json.dumps({"profile": legacy_profile})),
        )
        db.commit()
        db.close()

        (source / "flow_bridge_jobs.json").write_text(
            json.dumps({"path": legacy_media}),
            encoding="utf-8",
        )
        (source / "flow_sessions.json").write_text(
            json.dumps({"profile": legacy_profile}),
            encoding="utf-8",
        )
        return source

    def test_migration_preserves_source_and_rewrites_target_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = self.make_fixture(root)
            target = root / "TH Media"

            source_db_hash = digest(source / "aihub.db")
            source_media_hash = digest(source / "generated_media" / "clip.bin")
            result = migrate(source, target)

            self.assertEqual(result["database_integrity"], "ok")
            self.assertTrue((target / "Database" / "aihub.db").exists())
            self.assertTrue((target / "Database" / "master.key").exists())
            self.assertTrue((target / "Media" / "generated_media" / "clip.bin").exists())
            self.assertTrue((target / "FlowProfile" / "Default" / "Preferences").exists())
            self.assertFalse((target / "FlowProfile" / "lockfile").exists())
            self.assertTrue((target / "FlowSessions" / "session-a" / "Preferences").exists())
            self.assertEqual(digest(source / "aihub.db"), source_db_hash)
            self.assertEqual(digest(source / "generated_media" / "clip.bin"), source_media_hash)
            self.assertEqual(
                digest(target / "Media" / "generated_media" / "clip.bin"),
                source_media_hash,
            )

            target_db = target / "Database" / "aihub.db"
            self.assertEqual(sqlite_integrity(target_db), "ok")
            with closing(sqlite3.connect(target_db)) as conn:
                row = conn.execute(
                    "SELECT file_path, metadata_json FROM items WHERE id=1"
                ).fetchone()
            self.assertEqual(
                row[0],
                str(target / "Media" / "generated_media" / "clip.bin"),
            )
            metadata = json.loads(row[1])
            self.assertEqual(metadata["profile"], str(target / "FlowProfile"))

            jobs = json.loads((target / "flow_bridge_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(
                jobs["path"],
                str(target / "Media" / "generated_media" / "clip.bin"),
            )
            registry = json.loads(
                (target / "Database" / "flow_sessions.json").read_text(encoding="utf-8")
            )
            self.assertEqual(registry["profile"], str(target / "FlowProfile"))
            manifest = json.loads(
                (target / "migration_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["database_integrity"], "ok")
            self.assertGreaterEqual(manifest["database_path_updates"], 1)

    def test_migration_rewrites_paths_recorded_in_an_unresolved_spelling(self):
        # Windows hands out short names such as C:\Users\RUNNER~1, and a legacy
        # database stores whatever spelling the writing process had. Rewriting
        # only the resolved root used to leave those rows pointing at nothing.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            alternate = Path(root.drive.lower() + str(root)[2:])
            self.assertNotEqual(str(alternate), str(root))
            source = self.make_fixture(alternate)
            target = alternate / "TH Media"

            result = migrate(source, target)

            self.assertGreaterEqual(result["database_path_updates"], 1)
            with closing(sqlite3.connect(root / "TH Media" / "Database" / "aihub.db")) as conn:
                row = conn.execute("SELECT file_path FROM items WHERE id=1").fetchone()
            self.assertEqual(
                row[0],
                str(root / "TH Media" / "Media" / "generated_media" / "clip.bin"),
            )

    def test_non_empty_target_is_rejected_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_fixture(root)
            target = root / "TH Media"
            target.mkdir()
            sentinel = target / "existing.txt"
            sentinel.write_text("keep-me", encoding="utf-8")

            source_db_hash = digest(source / "aihub.db")
            with self.assertRaisesRegex(RuntimeError, "Target đã có dữ liệu"):
                migrate(source, target)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(digest(source / "aihub.db"), source_db_hash)
            self.assertFalse(any(root.glob("TH Media.migration-staging-*")))

    def test_replace_target_keeps_previous_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.make_fixture(root)
            target = root / "TH Media"
            target.mkdir()
            (target / "existing.txt").write_text("old-runtime", encoding="utf-8")

            result = migrate(source, target, replace_target=True)

            previous = Path(result["previous_target_backup"])
            self.assertTrue(previous.exists())
            self.assertEqual(
                (previous / "existing.txt").read_text(encoding="utf-8"),
                "old-runtime",
            )
            self.assertTrue((target / "Database" / "aihub.db").exists())
            self.assertEqual(sqlite_integrity(target / "Database" / "aihub.db"), "ok")


if __name__ == "__main__":
    unittest.main()
