import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flow_bridge.config import ensure_config, load_config
from app.flow_store import save_flow_settings


def main() -> None:
    cfg = load_config()
    api_key = str(cfg.get("api_key") or "")
    if not api_key:
        api_key = "thflow_" + secrets.token_urlsafe(48)
        cfg = ensure_config(api_key=api_key)

    host = os.getenv("TH_MEDIA_FLOW_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("TH_MEDIA_FLOW_BRIDGE_PORT", "8765"))
    save_flow_settings(f"http://{host}:{port}", api_key, True)
    print("Flow Bridge local config synchronized with TH Media.")


if __name__ == "__main__":
    main()
