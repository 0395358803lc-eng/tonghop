from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / ".data"
DB_PATH = DATA_DIR / "aihub.db"
KEY_PATH = DATA_DIR / "master.key"
FRONTEND_DIST = ROOT / "frontend" / "dist"

DATA_DIR.mkdir(parents=True, exist_ok=True)
