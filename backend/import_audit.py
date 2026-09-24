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


def audit_plugins() -> list[dict]:
    """Prove the yt-dlp PoT providers are usable, not merely present.

    These plugins are PoTokenProvider subclasses discovered through the
    yt_dlp_plugins namespace, so a bundle that keeps the files but loses the
    namespace - or ships only the abstract base - would still import cleanly.
    Requiring a concrete provider is the end of the contract the app depends on:
    the extractor arg youtubepot-bgutilhttp resolves to BgUtilHTTPPTP.
    """
    import inspect

    from yt_dlp.extractor.youtube.pot.provider import PoTokenProvider
    from yt_dlp.plugins import load_all_plugins

    # The shared base module only defines the abstract provider; the http module
    # is the one the app's youtubepot-bgutilhttp arg resolves to, so that one must
    # provide a concrete, instantiable class.
    expectations = (
        ("yt_dlp_plugins.extractor.getpot_bgutil", False),
        ("yt_dlp_plugins.extractor.getpot_bgutil_http", True),
    )

    results = []
    try:
        load_all_plugins()
    except BaseException as exc:
        results.append({"plugin": "yt_dlp.plugins.load_all_plugins", "ok": False,
                        "classes": [], "error": f"{type(exc).__name__}: {exc}"})

    for module_name, needs_concrete in expectations:
        try:
            module = importlib.import_module(module_name)
            found = [
                obj
                for _, obj in inspect.getmembers(module, inspect.isclass)
                if issubclass(obj, PoTokenProvider) and obj is not PoTokenProvider
                and obj.__module__ == module.__name__
            ]
            classes = sorted(obj.__name__ for obj in found)
            concrete = [obj for obj in found if not inspect.isabstract(obj)]
            usable = bool(concrete) if needs_concrete else bool(found)
            results.append({
                "plugin": module_name,
                "ok": usable,
                "classes": classes,
                "error": None if usable else "no usable PoTokenProvider subclass defined",
            })
        except BaseException as exc:
            results.append({"plugin": module_name, "ok": False, "classes": [],
                            "error": f"{type(exc).__name__}: {exc}"})
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
