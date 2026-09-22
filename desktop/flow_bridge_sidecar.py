import os

import uvicorn

from flow_bridge.app import app


def main() -> None:
    host = os.getenv("TH_MEDIA_FLOW_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("TH_MEDIA_FLOW_BRIDGE_PORT", "8765"))
    log_level = os.getenv("TH_MEDIA_FLOW_BRIDGE_LOG_LEVEL", "info")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
