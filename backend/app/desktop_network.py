from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timezone

_CACHE_TTL = 3.0
_lock = threading.Lock()
_cache: dict | None = None
_cache_at = 0.0

_PROBES = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("www.google.com", 443),
)


def _forced_status() -> bool | None:
    raw = os.getenv("TH_MEDIA_NETWORK_FORCE", "").strip().lower()
    if raw in {"offline", "0", "false", "down"}:
        return False
    if raw in {"online", "1", "true", "up"}:
        return True
    return None


def internet_status(*, force: bool = False) -> dict:
    global _cache, _cache_at
    forced = _forced_status()
    if forced is not None:
        return {
            "ok": True,
            "online": forced,
            "forced": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": 0,
            "probe": "forced",
            "message": "Đã kết nối Internet" if forced else "Chưa kết nối Internet",
        }

    now = time.monotonic()
    with _lock:
        if not force and _cache is not None and now - _cache_at < _CACHE_TTL:
            return {**_cache, "cached": True}

    started = time.monotonic()
    online = False
    probe = None
    errors: list[str] = []
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=0.8):
                online = True
                probe = f"{host}:{port}"
                break
        except OSError as exc:
            errors.append(f"{host}:{port}={exc.__class__.__name__}")

    result = {
        "ok": True,
        "online": online,
        "forced": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "probe": probe,
        "message": "Đã kết nối Internet" if online else "Chưa kết nối Internet",
        "errors": errors[-3:] if not online else [],
    }
    with _lock:
        _cache = result
        _cache_at = time.monotonic()
    return result


def requires_internet(method: str, path: str) -> bool:
    method = method.upper()
    if not path.startswith("/api/"):
        return False

    exact = {
        ("POST", "/api/flow/test"),
        ("POST", "/api/flow/open-login"),
        ("GET", "/api/flow/projects"),
        ("GET", "/api/flow/capabilities/video"),
        ("GET", "/api/flow/capabilities/image"),
        ("POST", "/api/video/proxy/test"),
    }
    if (method, path) in exact:
        return True

    if path.startswith("/api/providers/") and method in {"POST", "GET"}:
        if path.endswith("/test") or path.endswith("/models"):
            return True

    if path.startswith("/api/chats/") and path.endswith("/messages") and method == "POST":
        return True

    if path == "/api/video/jobs" and method == "POST":
        return True

    if path.startswith("/api/flow/sessions/") and method == "POST":
        return True

    if path.startswith("/api/film/projects/") and method == "POST":
        remote_suffixes = (
            "/analyze",
            "/continuity-check",
            "/consistency/repair",
            "/production-gate",
            "/auto-repair",
            "/resources/generate",
            "/resources/qc",
            "/narrator/preview",
            "/narrator/apply",
            "/render/queue",
            "/pipeline/start",
            "/pipeline/resume",
            "/final/qc",
        )
        if path.endswith(remote_suffixes):
            return True
        if "/junctions/" in path and path.endswith("/retry"):
            return True
        if path.endswith("/junctions/check"):
            return True
        if "/pipeline/retry/" in path:
            return True

    if path.startswith("/api/film/render/jobs/") and path.endswith("/retry") and method == "POST":
        return True

    if path == "/api/film/capabilities/matrix" and method == "POST":
        return True

    return False
