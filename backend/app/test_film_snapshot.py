import unittest
from pathlib import Path
from unittest.mock import patch

from .config import DATA_DIR
from .db import init_db
from .film_acceptance_snapshot import (
    backfill_acceptance_snapshots,
    create_acceptance_snapshot,
    current_fingerprint,
    ensure_acceptance_snapshot,
    get_acceptance_snapshot,
    latest_acceptance_snapshot,
    list_acceptance_snapshots,
    mark_final_stale,
    propagate_canonical_change,
    propagate_scene_change,
    propagate_voice_change,
    public_snapshot,
    snapshot_hash,
    _stable_voice_profile,
)
from .film_boundary_store import get_pair_junction, upsert_junction
from .film_final_store import create_final_render, get_final_render
from .film_media_store import register_completed_media
from .film_scene_state_store import save_ledger, upsert_scene_state
from .film_store import append_film_scenes, create_film_project, delete_film_project
from .film_voice_profile_store import upsert_voice_profile


def _tiny_mp4(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    return path


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.project = create_film_project(
            "__snapshot__",
            "Day la kich ban kiem thu snapshot dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        self.pid = self.project["id"]
        self.folder = DATA_DIR / "generated_media" / "_snap" / self.pid
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)

    def tearDown(self):
        delete_film_project(self.pid)

    def _approve(self, scene_id, index, frames=True):
        path = self.folder / f"{scene_id}.bin"
        try:
            media = register_completed_media(
                project_id=self.pid,
                media_type="video",
                role="scene_video",
                file_path=_tiny_mp4(path),
                scene_id=scene_id,
                provider="test",
                mime_type="video/mp4",
                qc_status="passed",
                qc_score=90,
                select_if_passed=True,
                provider_job_id=f"{self.pid}-{scene_id}-{index}",
            )
        except Exception:
            media = {"id": f"media-{scene_id}", "is_selected": True, "status": "completed", "qc_status": "passed", "version": 1}
            upsert_scene_state(self.pid, scene_id, index, status="APPROVED", selected_media_id=media["id"], force=True)
            if frames:
                save_ledger(self.pid, scene_id, {
                    "selected_media_id": media["id"],
                    "accepted_first_frame": f"/frames/{scene_id}/first.jpg",
                    "accepted_last_frame": f"/frames/{scene_id}/last.jpg",
                })
            return media
        upsert_scene_state(self.pid, scene_id, index, status="APPROVED", selected_media_id=media["id"], force=True)
        if frames:
            save_ledger(self.pid, scene_id, {
                "selected_media_id": media["id"],
                "accepted_first_frame": f"/frames/{scene_id}/first.jpg",
                "accepted_last_frame": f"/frames/{scene_id}/last.jpg",
            })
        return media

    def test_snapshot_created_on_approval(self):
        self._approve("SCENE_001", 0)
        snap = create_acceptance_snapshot(self.pid, "SCENE_001")
        self.assertTrue(snap.get("id"))
        self.assertEqual(snap["scene_id"], "SCENE_001")
        self.assertTrue(snap.get("snapshot_hash"))

    def test_snapshot_immutable(self):
        self._approve("SCENE_001", 0)
        first = create_acceptance_snapshot(self.pid, "SCENE_001")
        raw = get_acceptance_snapshot(self.pid, first["id"])
        original_hash = raw["snapshot_hash"]
        original_payload = dict(raw["payload"])
        original_payload["prompt"] = "mutated"
        listed = list_acceptance_snapshots(self.pid, "SCENE_001")
        self.assertEqual(listed[0]["snapshot_hash"], original_hash)
        again = get_acceptance_snapshot(self.pid, first["id"])
        self.assertEqual(again["snapshot_hash"], original_hash)
        self.assertNotEqual((again.get("payload") or {}).get("prompt"), "mutated")

    def test_new_approval_creates_new_snapshot(self):
        self._approve("SCENE_001", 0)
        a = create_acceptance_snapshot(self.pid, "SCENE_001")
        upsert_scene_state(self.pid, "SCENE_001", 0, selected_media_id="media-new-version", force=True)
        b = create_acceptance_snapshot(self.pid, "SCENE_001")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(list_acceptance_snapshots(self.pid, "SCENE_001")), 2)

    def test_snapshot_hash_stable(self):
        self._approve("SCENE_001", 0)
        payload = current_fingerprint(self.pid, "SCENE_001")
        self.assertEqual(snapshot_hash(payload), snapshot_hash(payload))
        snap = create_acceptance_snapshot(self.pid, "SCENE_001")
        self.assertEqual(snap["snapshot_hash"], latest_acceptance_snapshot(self.pid, "SCENE_001")["snapshot_hash"])

    def test_snapshot_public_payload_hides_local_paths(self):
        public = public_snapshot({
            "id": "s1",
            "project_id": self.pid,
            "scene_id": "SCENE_001",
            "snapshot_hash": "abc",
            "created_at": "now",
            "payload": {
                "file_path_internal": "C:/secret/video.mp4",
                "canonical": [{"entity_id": "CHAR_001", "local_path": "C:/secret/char.jpg"}],
            },
        })
        self.assertNotIn("file_path_internal", public["payload"])
        self.assertNotIn("local_path", public["payload"]["canonical"][0])

    def test_backfill_approved_scene(self):
        self._approve("SCENE_001", 0)
        self._approve("SCENE_002", 1)
        self.assertEqual(list_acceptance_snapshots(self.pid, "SCENE_001"), [])
        result = backfill_acceptance_snapshots(self.pid)
        self.assertGreaterEqual(result["created_count"], 2)
        self.assertEqual(len(list_acceptance_snapshots(self.pid, "SCENE_001")), 1)

    def test_backfill_is_idempotent(self):
        self._approve("SCENE_001", 0)
        first = backfill_acceptance_snapshots(self.pid)
        second = backfill_acceptance_snapshots(self.pid)
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["skipped_count"], 1)
        self.assertEqual(len(list_acceptance_snapshots(self.pid, "SCENE_001")), 1)

    def test_ensure_acceptance_snapshot_idempotent(self):
        self._approve("SCENE_001", 0)
        a = ensure_acceptance_snapshot(self.pid, "SCENE_001")
        b = ensure_acceptance_snapshot(self.pid, "SCENE_001")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(list_acceptance_snapshots(self.pid, "SCENE_001")), 1)

    def test_backfill_invalid_approval_blocks(self):
        upsert_scene_state(self.pid, "SCENE_001", 0, status="APPROVED", force=True)
        result = backfill_acceptance_snapshots(self.pid)
        self.assertEqual(result["created_count"], 0)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["blocked"][0]["code"], "BACKFILL_BLOCKED")

    def test_canonical_change_marks_scene_stale(self):
        self._approve("SCENE_001", 0)
        resources = [{
            "resource_type": "character",
            "entity_id": "CHAR_001",
            "status": "locked",
            "metadata": {"asset_version": 1, "canonical_sha256": "aaa", "media_id": "c1"},
        }]
        with patch("app.film_acceptance_snapshot.list_project_resources", return_value=resources):
            create_acceptance_snapshot(self.pid, "SCENE_001")
            changed = propagate_canonical_change(self.pid, "character", "CHAR_001")
        self.assertTrue(changed)
        from .film_scene_state_store import get_scene_state
        self.assertEqual(get_scene_state(self.pid, "SCENE_001")["status"], "STALE")

    def test_voice_calibration_runtime_fields_do_not_change_stable_fingerprint(self):
        base = {
            "gender": "female",
            "voice_profile_id": "VOICE_CHAR_001",
            "acoustic_identity": {
                "backend": "sherpa-onnx",
                "model": "speaker.onnx",
                "embedding_dim": 512,
                "embedding_sha256": "abc123",
                "enrolled_scene_id": "SCENE_003",
                "enrolled_at": "2026-09-21T22:14:29+00:00",
                "threshold": 0.23,
                "threshold_source": "project_calibrated",
                "embedding_path": "speaker_embeddings/a.npy",
                "sample_rate": 16000,
                "embedding_strategy": "vad-longest",
            },
        }
        recalibrated = {
            **base,
            "acoustic_identity": {
                **base["acoustic_identity"],
                "enrolled_at": "2026-09-21T22:23:36+00:00",
                "threshold": 0.19,
                "threshold_source": "project_calibrated_v2",
                "embedding_path": "speaker_embeddings/b.npy",
                "sample_rate": 48000,
                "embedding_strategy": "new-vad",
            },
        }
        self.assertEqual(_stable_voice_profile(base), _stable_voice_profile(recalibrated))

    def test_voice_embedding_change_changes_stable_fingerprint(self):
        a = {
            "gender": "female",
            "acoustic_identity": {
                "backend": "sherpa-onnx",
                "model": "speaker.onnx",
                "embedding_dim": 512,
                "embedding_sha256": "aaa",
                "enrolled_scene_id": "SCENE_003",
            },
        }
        b = {
            **a,
            "acoustic_identity": {
                **a["acoustic_identity"],
                "embedding_sha256": "bbb",
            },
        }
        self.assertNotEqual(_stable_voice_profile(a), _stable_voice_profile(b))

    def test_voice_change_marks_scene_stale(self):
        self._approve("SCENE_001", 0)
        upsert_voice_profile(self.pid, "CHAR_001", {"gender": "female"})
        upsert_voice_profile(self.pid, "CHAR_001", {"gender": "male"})
        from .film_scene_state_store import get_scene_state
        self.assertEqual(get_scene_state(self.pid, "SCENE_001")["status"], "STALE")

    def test_narrator_voice_change_marks_voiceover_scene_stale(self):
        self._approve("SCENE_001", 0)
        project = {
            "id": self.pid,
            "scenes": [
                {"id": "SCENE_001", "characters": [], "voiceover": "Loi dan"},
                {"id": "SCENE_002", "characters": ["CHAR_001"], "voiceover": ""},
            ],
        }
        with patch("app.film_acceptance_snapshot.get_film_project", return_value=project):
            changed = propagate_voice_change(self.pid, "NARRATOR")
        self.assertEqual(len(changed), 1)
        from .film_scene_state_store import get_scene_state
        self.assertEqual(get_scene_state(self.pid, "SCENE_001")["status"], "STALE")

    def test_scene_stale_marks_junction_stale(self):
        self._approve("SCENE_001", 0)
        self._approve("SCENE_002", 1)
        upsert_junction(self.pid, "SCENE_001", "SCENE_002", status="PASS")
        propagate_scene_change(self.pid, "SCENE_001", "SCENE_EDIT")
        row = get_pair_junction(self.pid, "SCENE_001", "SCENE_002")
        self.assertEqual(row["status"], "STALE")

    def test_scene_stale_marks_final_stale(self):
        self._approve("SCENE_001", 0)
        create_final_render(self.pid, status="QC_PENDING")
        propagate_scene_change(self.pid, "SCENE_001", "SCENE_EDIT")
        row = get_final_render(self.pid)
        self.assertEqual(row["status"], "STALE")
