import subprocess
import unittest
from pathlib import Path

from .config import DATA_DIR
from .db import connect, init_db
from .film_acceptance_snapshot import mark_final_stale
from .film_boundary_store import upsert_junction
from .film_final_assembly import assemble_project, _ffmpeg
from .film_final_store import update_final_render
from .film_master_qc import (
    SILENCE_GAP_MIN_SEC,
    _parse_silence_durations,
    _suspicious_silence_seconds,
    evaluate_master_qc,
    run_master_qc,
)
from .film_media_store import get_media, register_completed_media
from .film_scene_state_store import upsert_scene_state
from .film_store import append_film_scenes, create_film_project, delete_film_project


def _clip(path: Path, color: str, audio: str = "sine") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio_src = "sine=frequency=440:sample_rate=48000" if audio == "sine" else "anullsrc=r=48000:cl=stereo"
    args = [
        _ffmpeg(), "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=1",
        "-f", "lavfi", "-i", audio_src,
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        str(path),
    ]
    if audio == "none":
        args = [
            _ffmpeg(), "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(path),
        ]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError((proc.stderr or "")[-400:])


class MasterQcTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.project = create_film_project(
            "__master_qc__",
            "Day la kich ban kiem thu master qc dai hon hai muoi ky tu.",
            "xkiro",
            "qwen/qwen3.5-397b-a17b:free",
            {"scene_duration": 8},
        )
        self.pid = self.project["id"]
        self.folder = DATA_DIR / "generated_media" / "_master_qc" / self.pid
        self.folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        delete_film_project(self.pid)

    def _scene_media(self, scene_id, color, selected=True, audio="sine"):
        path = self.folder / f"{scene_id}_{color}_{audio}.mp4"
        _clip(path, color, audio)
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
            provider_job_id=f"{self.pid}-{scene_id}-{color}-{audio}",
        )

    def _ready_two(self, color1="red", color2="green", audio="sine"):
        append_film_scenes(self.pid, [
            {"id": "SCENE_001", "title": "A", "characters": ["CHAR_001"]},
            {"id": "SCENE_002", "title": "B", "characters": ["CHAR_001"]},
        ], start_index=0)
        m1 = self._scene_media("SCENE_001", color1, audio=audio)
        m2 = self._scene_media("SCENE_002", color2, audio=audio)
        upsert_scene_state(self.pid, "SCENE_001", 0, status="APPROVED", selected_media_id=m1["id"], force=True)
        upsert_scene_state(self.pid, "SCENE_002", 1, status="APPROVED", selected_media_id=m2["id"], force=True)
        upsert_junction(self.pid, "SCENE_001", "SCENE_002", status="PASS", selected_previous_media_id=m1["id"], selected_next_media_id=m2["id"])
        return m1, m2

    def test_master_qc_short_natural_pauses_are_not_audio_gap(self):
        text = (
            "[silencedetect] silence_duration: 0.422729\n"
            "[silencedetect] silence_duration: 0.284562\n"
        )
        durations = _parse_silence_durations(text)
        self.assertEqual(durations, [0.422729, 0.284562])
        self.assertEqual(_suspicious_silence_seconds(durations), 0.0)
        self.assertGreater(SILENCE_GAP_MIN_SEC, max(durations))

    def test_master_qc_long_contiguous_silence_is_audio_gap(self):
        durations = [0.31, SILENCE_GAP_MIN_SEC + 0.2, 0.28]
        self.assertGreater(
            _suspicious_silence_seconds(durations),
            0.0,
        )

    def test_master_qc_missing_scene_fail(self):
        self._ready_two()
        assemble_project(self.pid)
        append_film_scenes(self.pid, [{"id": "SCENE_003", "title": "C", "characters": ["CHAR_001"]}], start_index=2)
        report = evaluate_master_qc(self.pid)
        self.assertFalse(report["passed"])
        self.assertIn("missing_scene", report["hard_gate"]["failed"])

    def test_master_qc_black_frame_fail(self):
        self._ready_two("black", "black")
        assemble_project(self.pid)
        report = evaluate_master_qc(self.pid)
        self.assertFalse(report["passed"])
        self.assertIn("black_frames", report["hard_gate"]["failed"])

    def test_master_qc_broken_file_fail(self):
        self._ready_two()
        result = assemble_project(self.pid)
        media = get_media(result["current"]["media_id"])
        Path(media["file_path"]).write_bytes(b"not-an-mp4")
        report = evaluate_master_qc(self.pid)
        self.assertFalse(report["passed"])
        self.assertIn("broken_file", report["hard_gate"]["failed"])

    def test_master_qc_audio_missing_fail(self):
        self._ready_two(audio="none")
        assemble_project(self.pid)
        report = evaluate_master_qc(self.pid)
        self.assertFalse(report["passed"])
        self.assertIn("audio_missing", report["hard_gate"]["failed"])

    def test_master_qc_duration_mismatch_fail(self):
        m1, _m2 = self._ready_two()
        assemble_project(self.pid)
        with connect() as conn:
            conn.execute("UPDATE film_generated_media SET duration_seconds=99 WHERE id=?", (m1["id"],))
        report = evaluate_master_qc(self.pid)
        self.assertFalse(report["passed"])
        self.assertIn("duration_integrity", report["hard_gate"]["failed"])

    def test_master_qc_pass_selects_final_video(self):
        self._ready_two()
        assembled = assemble_project(self.pid)
        self.assertFalse((assembled["current"]["media"] or {}).get("is_selected"))
        status = run_master_qc(self.pid)
        current = status["current"]
        self.assertEqual(current["status"], "APPROVED")
        self.assertTrue(current["media"]["is_selected"])
        self.assertEqual(current["media"]["qc_status"], "passed")

    def test_master_qc_fail_does_not_select_final_video(self):
        self._ready_two("black", "black")
        assembled = assemble_project(self.pid)
        media_id = assembled["current"]["media_id"]
        status = run_master_qc(self.pid)
        self.assertEqual(status["current"]["status"], "QC_FAILED")
        self.assertFalse(status["current"]["media"]["is_selected"])
        self.assertEqual(get_media(media_id)["qc_status"], "failed")

    def test_master_qc_stale_final_blocks(self):
        self._ready_two()
        assemble_project(self.pid)
        mark_final_stale(self.pid, "SCENE_STALE")
        with self.assertRaises(ValueError) as ctx:
            run_master_qc(self.pid)
        self.assertIn("STALE", str(ctx.exception))
