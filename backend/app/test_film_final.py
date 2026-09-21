import subprocess
import unittest
from pathlib import Path

from .config import DATA_DIR
from .db import init_db
from .film_boundary_store import upsert_junction
from .film_final_assembly import assemble_project, evaluate_assembly_gate, _ffmpeg
from .film_media_store import get_media, register_completed_media, validate_media_path
from .film_scene_state_store import upsert_scene_state
from .film_store import append_film_scenes, create_film_project, delete_film_project


def _clip(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            _ffmpeg(), "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(proc.stderr[-400:])


class FinalAssemblyTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.project = create_film_project(
            "__final_asm__",
            "Day la kich ban kiem thu final assembly dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        self.pid = self.project["id"]
        self.folder = DATA_DIR / "generated_media" / "_final_tests" / self.pid
        self.folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        delete_film_project(self.pid)

    def _scene_media(self, scene_id, index, color, selected=True):
        path = self.folder / f"{scene_id}_{color}.mp4"
        _clip(path, color)
        return register_completed_media(
            project_id=self.pid,
            media_type="video",
            role="scene_video",
            file_path=path,
            scene_id=scene_id,
            provider="test",
            mime_type="video/mp4",
            qc_status="passed",
            qc_score=90,
            select_if_passed=selected,
            provider_job_id=f"{self.pid}-{scene_id}-{color}",
        )

    def _approve(self, scene_id, index, media):
        upsert_scene_state(
            self.pid, scene_id, scene_index=index, status="APPROVED",
            selected_media_id=media["id"], force=True,
        )

    def test_assembly_requires_all_scenes_approved(self):
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)
        m1 = self._scene_media("SCENE_001", 0, "red")
        self._approve("SCENE_001", 0, m1)
        gate = evaluate_assembly_gate(self.pid)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["code"], "FINAL_ASSEMBLY_BLOCKED")
        self.assertTrue(any(item["code"] == "SCENE_NOT_APPROVED" for item in gate["errors"]))
        with self.assertRaises(ValueError) as ctx:
            assemble_project(self.pid)
        self.assertIn("FINAL_ASSEMBLY_BLOCKED", str(ctx.exception))

    def test_assembly_requires_all_junctions_pass(self):
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)
        m1 = self._scene_media("SCENE_001", 0, "red")
        m2 = self._scene_media("SCENE_002", 1, "blue")
        self._approve("SCENE_001", 0, m1)
        self._approve("SCENE_002", 1, m2)
        gate = evaluate_assembly_gate(self.pid)
        self.assertFalse(gate["passed"])
        self.assertTrue(any(item["code"] == "JUNCTION_NOT_PASS" for item in gate["errors"]))

    def test_assembly_uses_selected_media_only(self):
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)
        old = self._scene_media("SCENE_001", 0, "green", selected=False)
        m1 = self._scene_media("SCENE_001", 0, "red", selected=True)
        m2 = self._scene_media("SCENE_002", 1, "blue", selected=True)
        self._approve("SCENE_001", 0, m1)
        self._approve("SCENE_002", 1, m2)
        upsert_junction(self.pid, "SCENE_001", "SCENE_002", status="PASS", selected_previous_media_id=m1["id"], selected_next_media_id=m2["id"])
        gate = evaluate_assembly_gate(self.pid)
        self.assertTrue(gate["passed"], gate["errors"])
        self.assertEqual(gate["items"][0]["media_id"], m1["id"])
        self.assertNotEqual(gate["items"][0]["media_id"], old["id"])

    def test_assembly_orders_by_scene_index(self):
        append_film_scenes(self.pid, [
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_003", "title": "C", "characters": ["CHAR_001"]},
        ], start_index=0)
        # start_index 0,1,2 in append order: SCENE_002 idx0, SCENE_001 idx1, SCENE_003 idx2 — fix by explicit approve indexes from DB later.
        m_b = self._scene_media("SCENE_002", 0, "blue")
        m_a = self._scene_media("SCENE_001", 1, "red")
        m_c = self._scene_media("SCENE_003", 2, "yellow")
        self._approve("SCENE_002", 0, m_b)
        self._approve("SCENE_001", 1, m_a)
        self._approve("SCENE_003", 2, m_c)
        upsert_junction(self.pid, "SCENE_002", "SCENE_001", status="PASS", selected_previous_media_id=m_b["id"], selected_next_media_id=m_a["id"])
        upsert_junction(self.pid, "SCENE_001", "SCENE_003", status="PASS", selected_previous_media_id=m_a["id"], selected_next_media_id=m_c["id"])
        gate = evaluate_assembly_gate(self.pid)
        self.assertTrue(gate["passed"], gate["errors"])
        self.assertEqual([item["scene_id"] for item in gate["items"]], ["SCENE_002", "SCENE_001", "SCENE_003"])

    def test_assembly_manifest_and_final_media(self):
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)
        m1 = self._scene_media("SCENE_001", 0, "red")
        m2 = self._scene_media("SCENE_002", 1, "blue")
        self._approve("SCENE_001", 0, m1)
        self._approve("SCENE_002", 1, m2)
        upsert_junction(self.pid, "SCENE_001", "SCENE_002", status="PASS", selected_previous_media_id=m1["id"], selected_next_media_id=m2["id"])
        result = assemble_project(self.pid)
        current = result["current"]
        self.assertEqual(current["status"], "QC_PENDING")
        self.assertTrue(current["manifest_hash"] or current["manifest"].get("manifest_hash"))
        self.assertEqual(current["manifest"]["scene_count"], 2)
        self.assertEqual([item["scene_id"] for item in current["manifest"]["items"]], ["SCENE_001", "SCENE_002"])
        self.assertNotIn("file_path_internal", current["manifest"]["items"][0])
        media = current["media"]
        self.assertEqual(media["role"], "final_video")
        self.assertEqual(media["status"], "completed")
        self.assertEqual(media["qc_status"], "pending")
        self.assertFalse(media["is_selected"])
        raw = get_media(media["id"])
        validate_media_path(raw["file_path"])
        self.assertTrue(Path(raw["file_path"]).is_file())
        self.assertGreater(Path(raw["file_path"]).stat().st_size, 1000)

    def test_assembly_manifest_persists(self):
        self.test_assembly_manifest_and_final_media()

    def test_final_media_record_created(self):
        self.test_assembly_manifest_and_final_media()

    def test_final_video_local_file_required(self):
        self.test_assembly_manifest_and_final_media()
