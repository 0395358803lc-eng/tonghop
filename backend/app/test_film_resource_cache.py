import base64
import io
import unittest

from PIL import Image

from .data_isolation import IsolatedDataMixin, IsolatedDataTestCase
from .db import init_db
from .film_resource_store import (
    get_project_resource,
    save_canonical_asset,
    sync_project_resources,
    update_resource_binding,
)
from .film_store import create_film_project, delete_film_project, save_film_bible


class FilmResourceCacheTests(IsolatedDataTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        init_db()

    def setUp(self):
        self.project = create_film_project(
            "__resource_cache_test__",
            "Day la kich ban kiem thu resource cache dai hon hai muoi ky tu.",
            "xkiro",
            "test-model",
            {"scene_duration": 8},
        )
        save_film_bible(
            self.project["id"],
            {
                "project_title": "__resource_cache_test__",
                "characters": [{"id": "CHAR_CACHE", "name": "Cache Character"}],
                "locations": [],
                "props": [],
            },
        )
        sync_project_resources(self.project["id"], "flow")

    def tearDown(self):
        delete_film_project(self.project["id"])

    @staticmethod
    def _image_b64() -> str:
        buf = io.BytesIO()
        Image.new("RGB", (96, 96), (20, 80, 140)).save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def test_identical_canonical_content_reuses_existing_file(self):
        first = save_canonical_asset(
            self.project["id"],
            "character",
            "CHAR_CACHE",
            self._image_b64(),
            "first.png",
            "flow",
            status="pending",
        )
        second = save_canonical_asset(
            self.project["id"],
            "character",
            "CHAR_CACHE",
            self._image_b64(),
            "second.png",
            "flow",
            status="pending",
        )

        self.assertEqual(first["local_path"], second["local_path"])
        self.assertEqual(
            (first.get("metadata") or {}).get("asset_version"),
            (second.get("metadata") or {}).get("asset_version"),
        )
        self.assertTrue((second.get("metadata") or {}).get("content_deduplicated"))
        self.assertEqual((second.get("metadata") or {}).get("content_cache_hit_count"), 1)
        self.assertEqual(
            (second.get("metadata") or {}).get("content_hash_algorithm"),
            "sha256-normalized-jpeg",
        )

    def test_sync_normalizes_flow_media_id_into_provider_ref(self):
        update_resource_binding(
            self.project["id"],
            "character",
            "CHAR_CACHE",
            provider="flow",
            provider_ref="local-canonical://legacy/CHAR_CACHE/v1",
            metadata_patch={"flow_media_id": "flow-media-asset-123"},
        )
        sync_project_resources(self.project["id"], "flow")
        resource = get_project_resource(
            self.project["id"], "character", "CHAR_CACHE", "flow"
        )

        self.assertEqual(resource["provider_ref"], "flow-media-asset-123")
        self.assertEqual(
            (resource.get("metadata") or {}).get("provider_ref_kind"),
            "flow_media_id",
        )
        self.assertTrue(
            (resource.get("metadata") or {}).get("provider_ref_normalized")
        )


if __name__ == "__main__":
    unittest.main()
