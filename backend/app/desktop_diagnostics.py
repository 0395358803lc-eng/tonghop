from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from runtime_dependencies import ffmpeg_path, ffprobe_path, speaker_model_path

from . import config
from .config import DATA_DIR
from .db import connect
from .film_qc_service import get_qc_status
from .film_scene_state_store import list_active_runs
from .film_speaker_identity import speaker_identity_status
from .flow_bridge_client import test_flow_bridge
from .flow_store import get_flow_status
from .provider_store import list_saved

_DIAGNOSTICS_DIR = DATA_DIR / "Diagnostics"
_MAX_LOG_BYTES = 1024 * 1024
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_version(executable: str) -> str | None:
    try:
        proc = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        line = (proc.stdout or proc.stderr or "").splitlines()
        return line[0].strip() if proc.returncode == 0 and line else None
    except Exception:
        return None


def _webview2_version() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except Exception:
        return None
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
    ]
    for hive, base in roots:
        try:
            with winreg.OpenKey(hive, base) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, name) as item:
                            product = str(winreg.QueryValueEx(item, "name")[0] or "")
                            if "webview2" not in product.lower():
                                continue
                            version = str(winreg.QueryValueEx(item, "pv")[0] or "").strip()
                            if version:
                                return version
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _db_status() -> dict:
    integrity = "unavailable"
    user_version = None
    try:
        with connect() as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except Exception as exc:
        integrity = f"error:{type(exc).__name__}"
    return {
        "path": str(config.DB_PATH),
        "exists": config.DB_PATH.is_file(),
        "size_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.is_file() else 0,
        "integrity": integrity,
        "user_version": user_version,
    }
def _storage_status() -> dict | None:
    try:
        usage = shutil.disk_usage(DATA_DIR)
        return {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0.0,
        }
    except Exception:
        return None


def _runtime_file(path: Path) -> dict:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
    }


def _active_runs() -> list[dict]:
    safe = []
    for run in list_active_runs():
        safe.append({
            "id": run.get("id"),
            "project_id": run.get("project_id"),
            "status": run.get("status"),
            "current_scene_id": run.get("current_scene_id"),
            "current_scene_index": run.get("current_scene_index"),
            "stop_after_current": bool(run.get("stop_after_current")),
            "updated_at": run.get("updated_at"),
        })
    return safe
async def diagnostics_status() -> dict:
    ffmpeg = Path(ffmpeg_path())
    ffprobe = Path(ffprobe_path())
    speaker_path = speaker_model_path()
    flow = get_flow_status()
    flow_probe = None
    try:
        flow_probe = await test_flow_bridge()
    except Exception as exc:
        flow_probe = {"ok": False, "error": type(exc).__name__}
    session = (flow_probe or {}).get("session") or {}
    flow_authenticated = bool(
        (flow_probe or {}).get("authenticated")
        or session.get("authenticated")
        or session.get("account_authenticated")
    )
    providers = sorted(list(list_saved().keys()))
    qc = get_qc_status()
    speaker = speaker_identity_status()
    return {
        "generated_at": _now(),
        "app_version": os.getenv("TH_MEDIA_APP_VERSION", "0.1.0"),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "webview2_version": _webview2_version(),
        "data_dir": str(DATA_DIR),
        "database": _db_status(),
        "storage": _storage_status(),
        "ffmpeg": {**_runtime_file(ffmpeg), "version": _command_version(str(ffmpeg))},
        "ffprobe": {**_runtime_file(ffprobe), "version": _command_version(str(ffprobe))},
        "speaker_model": _runtime_file(speaker_path),
        "speaker": {
            "enabled": bool(speaker.get("enabled")),
            "required": bool(speaker.get("required")),
            "model_exists": bool(speaker.get("model_exists")),
            "backend": speaker.get("backend"),
            "embedding_strategy": speaker.get("embedding_strategy"),
        },
        "flow": {
            "configured": bool(flow.get("configured")),
            "enabled": bool(flow.get("enabled")),
            "reachable": bool((flow_probe or {}).get("ok")),
            "authenticated": flow_authenticated,
            "session_state": session.get("state"),
            "project_usable": bool(session.get("project_usable")),
        },
        "ai": {
            "configured_providers": providers,
            "configured_provider_count": len(providers),
        },
        "qc": {
            "id": qc.get("id"),
            "name": qc.get("name"),
            "configured": bool(qc.get("configured")),
            "provider": qc.get("provider"),
            "model": qc.get("model"),
            "min_score": qc.get("min_score"),
        },
        "runtime": {
            "desktop_mode": os.getenv("TH_MEDIA_DESKTOP_MODE") == "1",
            "active_runs": _active_runs(),
            "active_pipeline_count": len(list_active_runs()),
        },
    }


_SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(x-th-media-token\s*[:=]\s*)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(flow_bridge_api_key\s*[:=]\s*)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
]
def _redact_log(text: str) -> str:
    value = text
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _safe_log_files() -> list[Path]:
    log_dir = DATA_DIR / "Logs"
    if not log_dir.is_dir():
        return []
    allowed = []
    for prefix in ("backend.log", "flow-bridge.log"):
        for path in sorted(log_dir.glob(f"{prefix}*")):
            if path.is_file():
                allowed.append(path)
    return allowed[:20]


async def export_diagnostics_bundle() -> Path:
    _DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = _DIAGNOSTICS_DIR / f"th-media-diagnostics_{stamp}.zip"
    status = await diagnostics_status()
    manifest = {
        "format": "TH Media Diagnostics v1",
        "privacy": {
            "database_included": False,
            "keys_included": False,
            "cookies_included": False,
            "flow_profile_included": False,
            "project_content_included": False,
            "logs_redacted": True,
        },
        "diagnostics": status,
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in _safe_log_files():
            try:
                data = path.read_bytes()[-_MAX_LOG_BYTES:]
                text = data.decode("utf-8", "replace")
                archive.writestr(f"logs/{path.name}", _redact_log(text))
            except Exception:
                continue
    return output


def validate_diagnostics_filename(filename: str) -> Path:
    safe = Path(filename).name
    if safe != filename or not safe.startswith("th-media-diagnostics_") or not safe.endswith(".zip"):
        raise ValueError("DIAGNOSTICS_FILE_INVALID")
    path = (_DIAGNOSTICS_DIR / safe).resolve()
    if path.parent != _DIAGNOSTICS_DIR.resolve() or not path.is_file():
        raise ValueError("DIAGNOSTICS_FILE_NOT_FOUND")
    return path
