"""Regression tests for the restart/update acceptance measurement helpers.

The acceptance gates only mean something if the measurement is stable when it
should be and sensitive when it should not be, so these tests pin both halves:
timestamps and rotating runtime secrets must not move the fingerprint, while
user data changes and asset byte changes must move it.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "desktop" / "scripts" / "acceptance"
SCHEMA = [
    """CREATE TABLE film_projects (id TEXT PRIMARY KEY, name TEXT, provider TEXT, model TEXT,
        status TEXT, stage TEXT, progress INTEGER, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE film_generated_media (id TEXT PRIMARY KEY, project_id TEXT, resource_type TEXT,
        media_type TEXT, role TEXT, status TEXT, file_path TEXT, thumbnail_path TEXT, file_size INTEGER,
        qc_status TEXT, is_selected INTEGER DEFAULT 0, updated_at TEXT)""",
    "CREATE TABLE film_render_jobs (id TEXT PRIMARY KEY, project_id TEXT, scene_index INTEGER, status TEXT)",
    "CREATE TABLE film_pipeline_runs (id TEXT PRIMARY KEY, project_id TEXT, status TEXT)",
    """CREATE TABLE film_final_renders (id TEXT PRIMARY KEY, project_id TEXT, version INTEGER,
        status TEXT, media_id TEXT, manifest_hash TEXT)""",
    "CREATE TABLE film_render_queues (project_id TEXT, adapter TEXT, paused INTEGER)",
    "CREATE TABLE provider_keys (provider TEXT PRIMARY KEY, encrypted_key TEXT, base_url TEXT, updated_at TEXT)",
    "CREATE TABLE secure_settings (key TEXT PRIMARY KEY, encrypted_value TEXT, updated_at TEXT)",
]


class AcceptanceFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        local_app_data = Path(self._temp.name).resolve() / "AppData"
        self.root = local_app_data / "TH Media" / "Desktop"
        (self.root / "Database").mkdir(parents=True)
        self._env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(local_app_data)
        self.addCleanup(lambda: os.environ.__setitem__("LOCALAPPDATA", self._env or ""))
        (self.db_path, self.db) = self._open_db(self.root / "Database" / "aihub.db")

    def tearDown(self):
        self.db.close()

    def _open_db(self, path: Path):
        connection = sqlite3.connect(path)
        for statement in SCHEMA:
            connection.execute(statement)
        connection.commit()
        return path, connection

    def run_helper(self, script: str, *args: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(HELPERS / script), *args],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, f"{script} stderr: {completed.stderr}")
        return json.loads(completed.stdout)

    def seed(self):
        self.db.execute(
            "INSERT INTO film_projects VALUES ('p1','Phim demo','openai','gpt',?,?,0,?,?)",
            ("ready", "Chờ render", "2026-01-01", "2026-01-01"),
        )
        media = self.root / "Media" / "generated_media"
        media.mkdir(parents=True, exist_ok=True)
        clip = media / "clip.mp4"
        clip.write_bytes(b"media-payload")
        self.db.execute(
            "INSERT INTO film_generated_media VALUES ('m1','p1','video','video','scene','done',?,NULL,?,?,1,?)",
            (str(clip), clip.stat().st_size, "APPROVED", "2026-01-01"),
        )
        self.db.execute("INSERT INTO film_final_renders VALUES ('f1','p1',1,'APPROVED','m1','hash1')")
        self.db.execute("INSERT INTO provider_keys VALUES ('openai','CIPHERTEXT-1','https://api',?)", ("2026-01-01",))
        self.db.execute("INSERT INTO secure_settings VALUES ('flow_runtime_token','ROTATING-1','2026-01-01')")
        self.db.commit()


class RestartFingerprintTests(AcceptanceFixture):
    def test_fingerprint_is_stable_across_reads(self):
        self.seed()
        first = self.run_helper("restart_fingerprint.py")
        second = self.run_helper("restart_fingerprint.py")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual([p["id"] for p in first["projects"]], ["p1"])
        self.assertEqual([m["id"] for m in first["selected_media"]], ["m1"])
        self.assertEqual([f["id"] for f in first["finals"]], ["f1"])

    def test_timestamps_and_rotating_secret_do_not_move_fingerprint(self):
        self.seed()
        before = self.run_helper("restart_fingerprint.py")["fingerprint"]
        self.db.execute("UPDATE film_projects SET updated_at='2026-09-24T09:00:00'")
        self.db.execute("UPDATE secure_settings SET encrypted_value='ROTATING-2', updated_at='2026-09-24'")
        self.db.commit()
        after = self.run_helper("restart_fingerprint.py")["fingerprint"]
        self.assertEqual(before, after)

    def test_user_data_change_moves_fingerprint(self):
        self.seed()
        before = self.run_helper("restart_fingerprint.py")["fingerprint"]
        self.db.execute("UPDATE film_projects SET status='rendering'")
        self.db.commit()
        self.assertNotEqual(before, self.run_helper("restart_fingerprint.py")["fingerprint"])

    def test_evidence_output_carries_no_key_material(self):
        self.seed()
        payload = self.run_helper("restart_fingerprint.py")
        self.assertNotIn("CIPHERTEXT-1", json.dumps(payload))
        self.assertNotIn("ROTATING-1", json.dumps(payload))
        self.assertEqual(payload["providers"][0]["provider"], "openai")
        self.assertEqual(payload["secure_setting_keys"], ["flow_runtime_token"])

    def test_flow_profile_state_ignores_browser_lock_noise(self):
        self.seed()
        profile = self.root / "FlowProfile" / "Default"
        profile.mkdir(parents=True)
        (profile / "lockfile").write_text("x", encoding="utf-8")
        self.assertFalse(self.run_helper("restart_fingerprint.py")["flow_profile_nonempty"])
        (profile / "Preferences").write_text('{"session":true}', encoding="utf-8")
        self.assertTrue(self.run_helper("restart_fingerprint.py")["flow_profile_nonempty"])


class AssetFingerprintTests(AcceptanceFixture):
    def test_selected_and_final_files_report_absolute_paths_and_digests(self):
        self.seed()
        assets = self.run_helper("update_asset_fingerprint.py")
        self.assertEqual(len(assets["selected_media_files"]), 1)
        entry = assets["selected_media_files"][0]["file"]
        self.assertTrue(entry["exists"])
        self.assertEqual(entry["path"], str(self.root / "Media" / "generated_media" / "clip.mp4"))
        self.assertEqual(entry["digest_mode"], "full")
        self.assertEqual(assets["final_files"][0]["file"]["digest"], entry["digest"])

    def test_fingerprint_follows_asset_bytes(self):
        self.seed()
        before = self.run_helper("update_asset_fingerprint.py")["fingerprint"]
        (self.root / "Media" / "generated_media" / "clip.mp4").write_bytes(b"changed-payload")
        self.assertNotEqual(before, self.run_helper("update_asset_fingerprint.py")["fingerprint"])

    def test_missing_asset_file_is_reported_not_raised(self):
        self.seed()
        (self.root / "Media" / "generated_media" / "clip.mp4").unlink()
        assets = self.run_helper("update_asset_fingerprint.py")
        self.assertFalse(assets["selected_media_files"][0]["file"]["exists"])
        self.assertIsNone(assets["selected_media_files"][0]["file"]["digest"])


class RewriteClonedPathsTests(AcceptanceFixture):
    def build_clone(self) -> Path:
        self.seed()
        # The cloned rows carry whatever spelling the producing process had; a
        # lowercase drive letter stands in for a Windows short name here.
        source_spelling = str(self.root).replace(self.root.drive, self.root.drive.lower(), 1)
        self.source_spelling = source_spelling
        recorded = Path(source_spelling) / "Media" / "generated_media" / "clip.mp4"
        self.db.execute("UPDATE film_generated_media SET file_path = ?", (str(recorded),))
        self.db.commit()

        clone = Path(self._temp.name).resolve() / "clone" / "TH Media" / "Desktop"
        (clone / "Database").mkdir(parents=True)
        (clone / "Media" / "generated_media").mkdir(parents=True)
        (clone / "Media" / "generated_media" / "clip.mp4").write_bytes(b"media-payload")
        (clone / "Database" / "aihub.db").write_bytes((self.root / "Database" / "aihub.db").read_bytes())
        (clone / "flow_bridge_jobs.json").write_text(
            json.dumps({"path": str(recorded)}), encoding="utf-8"
        )
        return clone

    def query_clone(self, clone: Path) -> str:
        with closing(sqlite3.connect(clone / "Database" / "aihub.db")) as connection:
            return str(connection.execute("SELECT file_path FROM film_generated_media").fetchone()[0])

    def test_rewrites_for_both_caller_and_resolved_spellings(self):
        clone = self.build_clone()
        source_spelling = self.source_spelling
        result = self.run_helper("rewrite_cloned_paths.py", source_spelling, str(clone))

        self.assertGreaterEqual(result["database_replacements"], 1)
        self.assertIn("flow_bridge_jobs.json", result["json_files_rewritten"])
        rewritten = self.query_clone(clone)
        self.assertTrue(rewritten.startswith(str(clone)), rewritten)
        self.assertNotIn(source_spelling.lower(), rewritten.lower())
        with closing((clone / "flow_bridge_jobs.json").open(encoding="utf-8")) as handle:
            payload = json.load(handle)
        self.assertTrue(payload["path"].startswith(str(clone)), payload["path"])

    def test_second_pass_changes_nothing(self):
        clone = self.build_clone()
        self.run_helper("rewrite_cloned_paths.py", self.source_spelling, str(clone))
        again = self.run_helper("rewrite_cloned_paths.py", self.source_spelling, str(clone))
        self.assertEqual(again["database_replacements"], 0)
        self.assertEqual(again["json_files_rewritten"], [])
        self.assertTrue(self.query_clone(clone).startswith(str(clone)))


if __name__ == "__main__":
    unittest.main()
