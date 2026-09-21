import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / ".data"
CONFIG_PATH = DATA_DIR / "flow_bridge_config.json"
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


def ensure_config(api_key: str) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = load_config()
    payload = {
        "api_key": api_key,
        "cdp_url": current["cdp_url"],
        "flow_url": current["flow_url"],
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
