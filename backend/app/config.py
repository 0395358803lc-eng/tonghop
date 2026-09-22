import os
import sys
from pathlib import Path


def _runtime_path(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    value = Path(os.path.expandvars(raw)).expanduser() if raw else default
    return value.resolve()


SOURCE_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_ROOT
ROOT = _runtime_path("TH_MEDIA_ROOT", RUNTIME_ROOT)
DATA_DIR = _runtime_path("TH_MEDIA_DATA_DIR", ROOT / ".data")
DB_PATH = _runtime_path("TH_MEDIA_DB_PATH", DATA_DIR / "aihub.db")
KEY_PATH = _runtime_path("TH_MEDIA_KEY_PATH", DATA_DIR / "master.key")
FRONTEND_DIST = _runtime_path("TH_MEDIA_FRONTEND_DIST", ROOT / "frontend" / "dist")

DATA_DIR.mkdir(parents=True, exist_ok=True)
