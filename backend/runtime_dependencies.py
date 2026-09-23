from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SPEAKER_MODEL_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def _existing(value: str | os.PathLike[str] | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_file() else None


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    project_root = Path(__file__).resolve().parents[1]
    roots.append(project_root / "desktop" / "runtime")
    return roots
def resolve_executable(name: str) -> str:
    env_key = f"TH_MEDIA_{name.upper()}_PATH"
    explicit = _existing(os.getenv(env_key))
    if explicit:
        return str(explicit)

    exe_name = f"{name}.exe" if os.name == "nt" else name
    candidates: list[Path] = []
    runtime_bin = os.getenv("TH_MEDIA_RUNTIME_BIN_DIR")
    if runtime_bin:
        candidates.append(Path(runtime_bin) / exe_name)

    data_dir = os.getenv("TH_MEDIA_DATA_DIR")
    if data_dir:
        candidates.append(Path(data_dir) / "Bin" / exe_name)

    for root in _runtime_roots():
        candidates.extend([
            root / exe_name,
            root / "bin" / exe_name,
            root / "runtime" / "bin" / exe_name,
        ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} không có trong TH Media runtime hoặc PATH.")
def ffmpeg_path() -> str:
    return resolve_executable("ffmpeg")


def ffprobe_path() -> str:
    return resolve_executable("ffprobe")


def speaker_model_path() -> Path:
    explicit = _existing(os.getenv("FILM_SPEAKER_MODEL_PATH"))
    if explicit:
        return explicit

    candidates: list[Path] = []
    data_dir = os.getenv("TH_MEDIA_DATA_DIR")
    if data_dir:
        base = Path(data_dir)
        candidates.extend([
            base / "Models" / "speaker" / SPEAKER_MODEL_NAME,
            base / "models" / "speaker" / SPEAKER_MODEL_NAME,
        ])

    runtime_models = os.getenv("TH_MEDIA_RUNTIME_MODEL_DIR")
    if runtime_models:
        candidates.append(Path(runtime_models) / "speaker" / SPEAKER_MODEL_NAME)

    for root in _runtime_roots():
        candidates.extend([
            root / "models" / "speaker" / SPEAKER_MODEL_NAME,
            root / "runtime" / "models" / "speaker" / SPEAKER_MODEL_NAME,
        ])

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    if data_dir:
        return (Path(data_dir) / "Models" / "speaker" / SPEAKER_MODEL_NAME).resolve()
    return candidates[0].resolve() if candidates else Path(SPEAKER_MODEL_NAME).resolve()
