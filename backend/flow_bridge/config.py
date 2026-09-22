import json
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
CONFIG_PATH = _runtime_path("TH_MEDIA_FLOW_CONFIG_PATH", DATA_DIR / "flow_bridge_config.json")
DEFAULT_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_FLOW_URL = "https://flow.google.com/"

def load_config() -> dict:
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    return {
        "api_key": os.getenv("FLOW_BRIDGE_API_KEY") or data.get("api_key") or "",
        "cdp_url": os.getenv("FLOW_CDP_URL") or data.get("cdp_url") or DEFAULT_CDP_URL,
        "flow_url": os.getenv("FLOW_URL") or data.get("flow_url") or DEFAULT_FLOW_URL,
    }
