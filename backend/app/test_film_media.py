import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .config import DATA_DIR
from .data_isolation import IsolatedDataMixin, IsolatedDataTestCase
from .db import init_db
from .db import connect
from .film_media_service import apply_selection_to_pipeline, delete_project_media_files, make_thumbnail, select_production_media
from .film_media_store import (
    get_media_by_provider_job,
    list_media_versions,
    public_media,
    register_completed_media,
    project_media_root,
    select_media,
    validate_media_path,
)
from .film_resource_store import get_project_resource
from .film_store import create_film_project, delete_film_project


class FilmMediaFoundationTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        init_db()
        cls.project = create_film_project(
            "__media_viewer_test__",
            "Day la kich ban kiem thu media viewer dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        cls.folder = DATA_DIR / "generated_media" / "_tests"
        cls.folder.mkdir(parents=True, exist_ok=True)
        cls.image_v1 = cls.folder / "char001_v1.jpg"
        cls.image_v2 = cls.folder / "char001_v2.jpg"
        from PIL import Image
        Image.new("RGB", (64, 64), (30, 80, 120)).save(cls.image_v1, "JPEG")
        Image.new("RGB", (80, 80), (180, 40, 40)).save(cls.image_v2, "JPEG")

    @classmethod
    def tearDownClass(cls):
        delete_film_project(cls.project["id"])
        super().tearDownClass()

    def test_path_validation_blocks_escape(self):
        validate_media_path(self.image_v1)
        with self.assertRaises(ValueError):
            validate_media_path(r"C:\Windows\System32\drivers\etc\hosts")
        with self.assertRaises(ValueError):
            validate_media_path(DATA_DIR / ".." / "film_media_store.py")

    def test_legacy_data_root_allows_only_known_media_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "Desktop"
            media_root = data_root / "Media"
            legacy_video = data_root / "flow_downloads" / "job-1" / "result.mp4"
            database_file = data_root / "Database" / "aihub.db"
            media_root.mkdir(parents=True)
            legacy_video.parent.mkdir(parents=True)
            legacy_video.write_bytes(b"video")
            database_file.parent.mkdir(parents=True)
            database_file.write_bytes(b"db")
            with (
                patch("app.film_media_store.DATA_DIR", data_root),
                patch("app.film_media_store.MEDIA_DIR", media_root),
                patch("app.film_media_store.LEGACY_MEDIA_DIRS", (data_root,)),
            ):
                self.assertEqual(validate_media_path(legacy_video), legacy_video.resolve())
                with self.assertRaisesRegex(ValueError, "MEDIA_PATH_DENIED"):
                    validate_media_path(database_file)

    def test_completed_requires_local_file(self):
        with self.assertRaises(ValueError):
            register_completed_media(
                project_id=self.project["id"],
                media_type="image",
                role="canonical_image",
                file_path=self.folder / "missing.jpg",
                resource_type="character",
                entity_id="CHAR_TEST",
                provider_job_id="test-missing",
            )

    def test_versioning_and_select(self):
        first = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v1,
            resource_type="character",
            entity_id="CHAR_TEST",
            provider="flow",
            model="Nano Banana 2",
            provider_job_id="test-char-v1",
            mime_type="image/jpeg",
            qc_status="failed",
            qc_score=40,
            qc={"hard_gate": {"passed": False}},
            select_if_passed=False,
        )
        second = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v2,
            resource_type="character",
            entity_id="CHAR_TEST",
            provider="flow",
            model="Nano Banana 2",
            provider_job_id="test-char-v2",
            mime_type="image/jpeg",
            qc_status="passed",
            qc_score=98,
            qc={"hard_gate": {"passed": True}},
            select_if_passed=True,
        )
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        self.assertTrue(second["is_selected"])
        versions = list_media_versions(second["id"])
        self.assertGreaterEqual(len(versions), 2)
        with self.assertRaises(ValueError):
            select_media(first["id"])
        selected_count = sum(1 for item in versions if item["is_selected"])
        self.assertEqual(selected_count, 1)
        self.assertTrue(second["is_selected"])
        public = public_media(second)
        self.assertIsNone(public.get("file_path"))
        self.assertTrue(str(public["file_url"]).startswith("/api/film/media/"))
        self.assertNotIn("C:\\", json.dumps(public))

    def test_provider_job_idempotent(self):
        first = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v1,
            resource_type="prop",
            entity_id="PROP_TEST",
            provider_job_id="same-job-once",
            mime_type="image/jpeg",
            qc_status="passed",
            qc_score=90,
            qc={"hard_gate": {"passed": True}},
        )
        again = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v1,
            resource_type="prop",
            entity_id="PROP_TEST",
            provider_job_id="same-job-once",
            mime_type="image/jpeg",
            qc_status="passed",
            qc_score=91,
            qc={"hard_gate": {"passed": True}},
        )
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(get_media_by_provider_job("same-job-once")["id"], first["id"])

    def test_failed_media_cannot_select(self):
        failed = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v1,
            resource_type="character",
            entity_id="CHAR_FAIL_SELECT",
            provider_job_id="fail-select-v1",
            mime_type="image/jpeg",
            qc_status="failed",
            qc_score=40,
            qc={"hard_gate": {"passed": False}},
            select_if_passed=False,
        )
        with self.assertRaises(ValueError):
            select_media(failed["id"])

    def test_repair_candidate_cannot_select(self):
        candidate = register_completed_media(
            project_id=self.project["id"],
            media_type="video",
            role="repair_candidate",
            file_path=self.image_v1,
            scene_id="SCENE_FAIL",
            provider_job_id="repair-select-v1",
            mime_type="image/jpeg",
            qc_status="failed",
            qc_score=71,
            qc={"hard_gate": {"passed": False}},
            select_if_passed=False,
        )
        with self.assertRaises(ValueError):
            select_media(candidate["id"])

    def test_thumbnail_uniqueness(self):
        folder_a = DATA_DIR / "generated_media" / "_thumbs_a"
        folder_b = DATA_DIR / "generated_media" / "_thumbs_b"
        folder_a.mkdir(parents=True, exist_ok=True)
        folder_b.mkdir(parents=True, exist_ok=True)
        path_a = folder_a / "result.mp4"
        path_b = folder_b / "result.mp4"
        path_a.write_bytes(self.image_v1.read_bytes())
        path_b.write_bytes(self.image_v2.read_bytes())
        thumb_a = make_thumbnail(path_a, "image", key="job-aaa")
        thumb_b = make_thumbnail(path_b, "image", key="job-bbb")
        self.assertIsNotNone(thumb_a)
        self.assertIsNotNone(thumb_b)
        self.assertNotEqual(str(thumb_a), str(thumb_b))
        self.assertTrue(thumb_a.exists())
        self.assertTrue(thumb_b.exists())

    def test_project_media_root_and_delete_are_isolated(self):
        project_a = create_film_project('__media_isolation_a__', 'Noi dung du an A dai hon hai muoi ky tu.', 'xkiro', 'test', {})
        project_b = create_film_project('__media_isolation_b__', 'Noi dung du an B dai hon hai muoi ky tu.', 'xkiro', 'test', {})
        try:
            root_a = project_media_root(project_a['id'])
            root_b = project_media_root(project_b['id'])
            file_a = root_a / 'flow_downloads' / 'job-a' / 'result.mp4'
            file_b = root_b / 'flow_downloads' / 'job-b' / 'result.mp4'
            file_a.parent.mkdir(parents=True, exist_ok=True)
            file_b.parent.mkdir(parents=True, exist_ok=True)
            file_a.write_bytes(b'a')
            file_b.write_bytes(b'b')
            self.assertNotEqual(root_a, root_b)
            result = delete_project_media_files(project_a['id'])
            self.assertGreaterEqual(result['removed_dirs'], 1)
            self.assertFalse(root_a.exists())
            self.assertTrue(file_b.exists())
        finally:
            delete_project_media_files(project_a['id'])
            delete_project_media_files(project_b['id'])
            delete_film_project(project_a['id'])
            delete_film_project(project_b['id'])

    def test_selection_propagation(self):
        import uuid
        resource_id = str(uuid.uuid4())
        with connect() as conn:
            conn.execute(
                """INSERT INTO film_provider_resources(
                id,project_id,provider,resource_type,entity_id,fingerprint,status,local_path,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (resource_id, self.project["id"], "flow", "character", "CHAR_PROPAGATE", "fp", "ready", str(self.image_v1), "{}"),
            )
            conn.execute(
                "INSERT INTO film_scenes(id,project_id,scene_index,title,duration) VALUES(?,?,?,?,?)",
                ("SCENE_PROP", self.project["id"], 99, "Scene prop", 8),
            )
        passed = register_completed_media(
            project_id=self.project["id"],
            media_type="image",
            role="canonical_image",
            file_path=self.image_v2,
            resource_type="character",
            entity_id="CHAR_PROPAGATE",
            provider_job_id="propagate-char-v2",
            mime_type="image/jpeg",
            qc_status="passed",
            qc_score=96,
            qc={"hard_gate": {"passed": True}},
            select_if_passed=True,
        )
        selected = select_production_media(passed["id"])
        resource = get_project_resource(self.project["id"], "character", "CHAR_PROPAGATE", "flow")
        self.assertEqual(resource["media_id"], selected["id"])
        self.assertEqual(Path(resource["local_path"]).resolve(), Path(self.image_v2).resolve())
        self.assertEqual((resource.get("metadata") or {}).get("selected_media_id"), selected["id"])
        video = register_completed_media(
            project_id=self.project["id"],
            media_type="video",
            role="scene_video",
            file_path=self.image_v2,
            scene_id="SCENE_PROP",
            provider_job_id="propagate-scene-v3",
            mime_type="video/mp4",
            qc_status="passed",
            qc_score=96,
            qc={"hard_gate": {"passed": True}},
            metadata={"render_job_id": "job-scene-prop"},
            select_if_passed=True,
        )
        apply_selection_to_pipeline(video)
        with connect() as conn:
            scene = conn.execute(
                "SELECT result_url FROM film_scenes WHERE project_id=? AND id=?",
                (self.project["id"], "SCENE_PROP"),
            ).fetchone()
        self.assertEqual(scene["result_url"], f"/api/film/media/{video['id']}/file")


if __name__ == "__main__":
    unittest.main()
