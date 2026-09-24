"""Import audit for the frozen TH Media sidecars.

PyInstaller only sees imports it can follow statically, so a module reached
through a lazy or dynamic import can be missing from the bundle and fail on the
user's first real action instead of at build time. Running this inside the
packaged executable checks the contract that actually matters: the modules the
application imports later are importable in the frozen environment.
"""

from __future__ import annotations

import importlib
import json
import time

# Imported while serving requests, so a missing one breaks a feature silently.
# The PoT solver ships as yt-dlp namespace plugins, which yt-dlp discovers at
# runtime, so PyInstaller cannot see them and the audit must.
BACKEND_MODULES = [
    "fastapi",
    "uvicorn",
    "httpx",
    "pydantic",
    "cryptography.fernet",
    "psutil",
    "yt_dlp",
    "yt_dlp_plugins.extractor.getpot_bgutil",
    "yt_dlp_plugins.extractor.getpot_bgutil_http",
    "PIL.Image",
    "av",
    "faster_whisper",
    "sherpa_onnx",
]

FLOW_MODULES = [
    "fastapi",
    "uvicorn",
    "httpx",
    "playwright.sync_api",
    "PIL.Image",
]


def audit(modules: list[str]) -> list[dict]:
    results = []
    for name in modules:
        started = time.perf_counter()
        try:
            importlib.import_module(name)
            record = {"module": name, "ok": True, "error": None}
        except BaseException as exc:  # a broken optional dependency must not abort the audit
            record = {"module": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        record["took_ms"] = round((time.perf_counter() - started) * 1000)
        results.append(record)
    return results


def run(role: str, extra: list[str] | None = None) -> int:
    modules = BACKEND_MODULES if role == "backend" else FLOW_MODULES
    results = audit(modules + (extra or []))
    failed = [item["module"] for item in results if not item["ok"]]
    print(json.dumps({
        "role": role,
        "checked": len(results),
        "failed": failed,
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0
