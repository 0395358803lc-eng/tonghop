import os
import sys

import uvicorn

import import_audit  # imported unconditionally so PyInstaller bundles it for --import-audit
from app.main import app


def main() -> None:
    if "--import-audit" in sys.argv:
        raise SystemExit(import_audit.run("backend"))
    host = os.getenv("TH_MEDIA_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("TH_MEDIA_BACKEND_PORT", "8012"))
    log_level = os.getenv("TH_MEDIA_BACKEND_LOG_LEVEL", "info")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
