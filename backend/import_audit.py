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


def _subclass_names(root: type) -> set[str]:
    """Every subclass below root, not just the direct children.

    BgUtilHTTPPTP extends BgUtilPTPBase rather than PoTokenProvider directly, so
    __subclasses__() on the base alone reports the abstract parent and hides the
    provider the application actually uses.
    """
    names: set[str] = set()
    pending = [root]
    while pending:
        for child in pending.pop().__subclasses__():
            if child.__name__ not in names:
                names.add(child.__name__)
                pending.append(child)
    return names


def audit_plugins() -> list[dict]:
    """Prove yt-dlp can load the PoT providers, the way the app does.

    The app never imports these modules: yt-dlp discovers them through the
    yt_dlp_plugins namespace and each plugin registers itself once under a unique
    provider name. Importing a plugin here first would double-register and raise
    "PoTokenProvider BgUtilHTTP already registered", so this mirrors production:
    let yt-dlp load, then check what it registered.
    """
    from yt_dlp.extractor.youtube.pot.provider import PoTokenProvider
    from yt_dlp.plugins import load_all_plugins

    results = []
    try:
        load_all_plugins()
        results.append({"plugin": "yt_dlp.plugins.load_all_plugins", "ok": True,
                        "classes": [], "error": None})
    except BaseException as exc:
        results.append({"plugin": "yt_dlp.plugins.load_all_plugins", "ok": False,
                        "classes": [], "error": f"{type(exc).__name__}: {exc}"})

    registered = sorted(_subclass_names(PoTokenProvider))
    for expected in ("BgUtilHTTPPTP",):
        results.append({
            "plugin": f"PoTokenProvider:{expected}",
            "ok": expected in registered,
            "classes": registered,
            "error": None if expected in registered else "provider not registered by yt-dlp",
        })
    return results


def run(role: str, extra: list[str] | None = None) -> int:
    modules = BACKEND_MODULES if role == "backend" else FLOW_MODULES
    results = audit(modules + (extra or []))
    failed = [item["module"] for item in results if not item["ok"]]
    payload: dict = {"role": role, "checked": len(results), "failed": failed, "results": results}
    if role == "backend":
        plugins = audit_plugins()
        payload["plugins"] = plugins
        failed.extend(item["plugin"] for item in plugins if not item["ok"])
        payload["failed"] = failed
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0
