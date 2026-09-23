import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_dependencies import SPEAKER_MODEL_NAME, resolve_executable, speaker_model_path


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


if __name__ == "__main__":
    unittest.main()
