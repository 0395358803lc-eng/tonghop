from pathlib import Path
import unittest

from PIL import Image

from .config import DATA_DIR
from .film_pipeline_service import (
    build_flow_reference_payload,
    resolve_reference_priority,
    sanitize_canonical_resource_manifest,
)

PREV_URL = "/api/flow/render/59b106cd-ff3b-42d2-9cc5-33e5d364c587/last-frame"
ASSET_ROOT = (DATA_DIR / "film_assets").resolve()


def _jpeg(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (40, 80, 120)).save(path, "JPEG")
    return str(path.resolve())


def _canonical_manifest():
    folder = ASSET_ROOT / "_r3_contract_test"
    return {
        "ready": True,
        "missing": [],
        "references": [
            {"resource_type": "character", "entity_id": "CHAR_001", "local_path": _jpeg(folder / "char.jpg")},
            {"resource_type": "location", "entity_id": "LOC_001", "local_path": _jpeg(folder / "loc.jpg")},
            {"resource_type": "prop", "entity_id": "PROP_001", "local_path": _jpeg(folder / "prop.jpg"), "critical": True, "critical_prop": True},
        ],
    }


def _bridge_resolve(manifest: dict):
    missing = manifest.get("missing") or []
    if missing or manifest.get("ready") is False:
        raise RuntimeError("RESOURCE_LOCK_INCOMPLETE")
    resolved = []
    for item in manifest.get("references") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("local_path") or "").strip()
        if not raw:
            continue
        path = Path(raw).resolve()
        try:
            path.relative_to(ASSET_ROOT)
        except ValueError as exc:
            raise RuntimeError("REFERENCE_ERROR: Canonical asset nằm ngoài thư mục film_assets.") from exc
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"REFERENCE_ERROR: Canonical asset không tồn tại: {item.get('entity_id')}")
        resolved.append((str(item.get("resource_type") or "other"), path))
    return resolved

class ReferenceContractTests(unittest.TestCase):
    def test_previous_last_frame_not_in_canonical_manifest(self):
        built = build_flow_reference_payload(_canonical_manifest(), PREV_URL, 4)
        kinds = [x.get("resource_type") for x in built["resource_manifest"]["references"]]
        buckets = (built.get("reference_selection") or {}).get("buckets") or []
        self.assertEqual(built["resolved"]["count"], 4)
        self.assertIn("previous_last_frame", buckets)
        self.assertNotIn("previous_last_frame", kinds)
        self.assertEqual(set(kinds), {"character", "location", "prop"})
        self.assertEqual(built["reference_image_url"], PREV_URL)
        self.assertEqual((built.get("boundary_reference") or {}).get("url"), PREV_URL)

    def test_scene1_has_no_boundary_reference(self):
        built = build_flow_reference_payload(_canonical_manifest(), None, 4)
        kinds = [x.get("resource_type") for x in built["resource_manifest"]["references"]]
        self.assertIsNone(built.get("reference_image_url"))
        self.assertIsNone(built.get("boundary_reference"))
        self.assertEqual(set(kinds), {"character", "location", "prop"})
        self.assertNotIn("previous_last_frame", (built.get("reference_selection") or {}).get("buckets") or [])
        _bridge_resolve(built["resource_manifest"])

    def test_scene2_reference_manifest_passes_bridge_validation(self):
        built = build_flow_reference_payload(_canonical_manifest(), PREV_URL, 4)
        resolved = _bridge_resolve(built["resource_manifest"])
        self.assertTrue(resolved)
        self.assertEqual({kind for kind, _path in resolved}, {"character", "location", "prop"})
        self.assertEqual(built["reference_image_url"], PREV_URL)
        mixed = sanitize_canonical_resource_manifest({
            "ready": True,
            "references": built["resource_manifest"]["references"] + [
                {"resource_type": "previous_last_frame", "local_path": PREV_URL}
            ],
        })
        self.assertNotIn("previous_last_frame", [x.get("resource_type") for x in mixed["references"]])
        _bridge_resolve(mixed)

    def test_capacity_still_counts_previous_last_frame(self):
        resolved = resolve_reference_priority(_canonical_manifest(), PREV_URL, 3)
        accounted = [x["bucket"] for x in (resolved["selected"] + resolved["dropped"])]
        self.assertEqual(resolved["count"], 4)
        self.assertIn("previous_last_frame", accounted)
        self.assertEqual([x["bucket"] for x in resolved["selected"]], ["character", "previous_last_frame", "location"])
        built = build_flow_reference_payload(_canonical_manifest(), PREV_URL, 3)
        kinds = [x.get("resource_type") for x in built["resource_manifest"]["references"]]
        self.assertEqual(kinds, ["character", "location"])
        self.assertEqual(built["reference_image_url"], PREV_URL)
        self.assertIn("previous_last_frame", built["reference_selection"]["buckets"])
        self.assertEqual(built["resolved"]["count"], 4)

    def test_bridge_rejects_previous_frame_if_left_in_canonical(self):
        with self.assertRaises(RuntimeError) as ctx:
            _bridge_resolve({
                "ready": True,
                "references": [
                    {"resource_type": "previous_last_frame", "local_path": PREV_URL},
                ],
            })
        self.assertIn("REFERENCE_ERROR", str(ctx.exception))
