import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_dependencies import (
    SPEAKER_MODEL_NAME,
    resolve_executable,
    speaker_model_path,
    whisper_model_path,
)


class RuntimeDependencyTests(unittest.TestCase):
    def test_explicit_ffmpeg_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ffmpeg.exe"
            binary.write_bytes(b"fake")
            with patch.dict(os.environ, {"TH_MEDIA_FFMPEG_PATH": str(binary)}, clear=False):
                self.assertEqual(Path(resolve_executable("ffmpeg")), binary.resolve())

    def test_runtime_bin_directory_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "ffprobe.exe"
            binary.write_bytes(b"fake")
            env = {"TH_MEDIA_RUNTIME_BIN_DIR": tmp, "TH_MEDIA_FFPROBE_PATH": ""}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(Path(resolve_executable("ffprobe")), binary.resolve())

    def test_desktop_models_directory_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "Models" / "speaker" / SPEAKER_MODEL_NAME
            model.parent.mkdir(parents=True)
            model.write_bytes(b"fake-model")
            env = {"TH_MEDIA_DATA_DIR": tmp, "FILM_SPEAKER_MODEL_PATH": ""}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(speaker_model_path(), model.resolve())

    def test_bundled_whisper_model_directory_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "whisper" / "base"
            model_dir.mkdir(parents=True)
            (model_dir / "model.bin").write_bytes(b"model")
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            env = {
                "TH_MEDIA_RUNTIME_MODEL_DIR": tmp,
                "TH_MEDIA_WHISPER_MODEL_DIR": "",
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(Path(whisper_model_path("base")), model_dir.resolve())

    def test_whisper_model_name_falls_back_for_development(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "TH_MEDIA_RUNTIME_MODEL_DIR": tmp,
                "TH_MEDIA_WHISPER_MODEL_DIR": "",
                "TH_MEDIA_DATA_DIR": tmp,
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch("runtime_dependencies._runtime_roots", return_value=[Path(tmp) / "runtime-empty"]),
            ):
                self.assertEqual(whisper_model_path("base"), "base")

    def test_packaged_sidecar_never_falls_back_to_hugging_face_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "TH_MEDIA_RUNTIME_MODEL_DIR": tmp,
                "TH_MEDIA_WHISPER_MODEL_DIR": "",
                "TH_MEDIA_DATA_DIR": tmp,
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch("runtime_dependencies._runtime_roots", return_value=[Path(tmp) / "runtime-empty"]),
                patch.object(sys, "frozen", True, create=True),
            ):
                with self.assertRaises(FileNotFoundError):
                    whisper_model_path("base")

    def test_packaged_sidecar_uses_bundled_dir_without_hugging_face_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "runtime" / "models" / "whisper" / "base"
            model_dir.mkdir(parents=True)
            (model_dir / "model.bin").write_bytes(b"model")
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            env = {
                "TH_MEDIA_RUNTIME_MODEL_DIR": str(root / "runtime" / "models"),
                "TH_MEDIA_WHISPER_MODEL_DIR": "",
                "TH_MEDIA_DATA_DIR": str(root / "empty-data"),
                "HF_HOME": str(root / "empty-hf-cache"),
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch("runtime_dependencies._runtime_roots", return_value=[]),
                patch.object(sys, "frozen", True, create=True),
            ):
                self.assertEqual(Path(whisper_model_path("base")), model_dir.resolve())


if __name__ == "__main__":
    unittest.main()
