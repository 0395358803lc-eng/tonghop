"""Shared measurement helpers for the TH Media restart/update acceptance gates.

These scripts deliberately measure only durable user state: the fingerprint must
survive an application restart and a version upgrade, so timestamps, rotating
runtime secrets and browser cache noise are excluded by design.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

FULL_DIGEST_LIMIT = 64 * 1024 * 1024
PARTIAL_EDGE = 8 * 1024 * 1024

FLOW_SKIP_DIRS = {
    "Crashpad", "ShaderCache", "GrShaderCache", "GraphiteDawnCache", "Code Cache",
    "GPUCache", "DawnGraphiteCache", "Default/Cache", "Default/Code Cache",
}
FLOW_SKIP_FILES = {
    "SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile",
    "RunningChromeVersion", "DevToolsActivePort",
}


def data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is required to locate the TH Media data root.")
    return Path(local) / "TH Media" / "Desktop"


def db_path() -> Path:
    return data_root() / "Database" / "aihub.db"


def connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"TH Media database is missing: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def text_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        if str(row[2] or "").upper().find("TEXT") >= 0 or str(row[2] or "").upper().find("CHAR") >= 0
    ]


def tables_present(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def digest_file(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size": 0,
        "digest": None,
        "digest_mode": "absent",
    }
    if not path.is_file():
        return record

    size = path.stat().st_size
    record["size"] = size
    if size > FULL_DIGEST_LIMIT:
        with path.open("rb") as handle:
            head = handle.read(PARTIAL_EDGE)
            if size > PARTIAL_EDGE * 2:
                handle.seek(-PARTIAL_EDGE, os.SEEK_END)
                tail = handle.read(PARTIAL_EDGE)
            else:
                tail = b""
        digest = hashlib.sha256(head + tail)
        record["digest"] = digest.hexdigest()
        record["digest_mode"] = "edges"
        return record

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    record["digest"] = digest.hexdigest()
    record["digest_mode"] = "full"
    return record


def resolve_recorded_path(root: Path, stored: str | None) -> Path | None:
    if not stored:
        return None
    candidate = Path(stored).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


def listed_file_records(root: Path, relative_dir: str) -> list[dict[str, Any]]:
    directory = root / relative_dir
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        record = digest_file(path)
        record["relative_path"] = path.relative_to(root).as_posix()
        records.append(record)
    return records


def flow_profile_has_state(root: Path) -> bool:
    profile = root / "FlowProfile"
    if not profile.is_dir():
        return False
    for path in profile.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(profile).parts)
        if parts & FLOW_SKIP_DIRS or path.name in FLOW_SKIP_FILES:
            continue
        if path.stat().st_size > 0:
            return True
    return False


def flow_session_state(root: Path) -> dict[str, Any]:
    registry = root / "Database" / "flow_sessions.json"
    if not registry.is_file():
        return {"active_id": None, "sessions": []}
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_id": None, "sessions": [], "error": "unreadable"}
    sessions = [
        {"id": item.get("id"), "name": item.get("name")}
        for item in payload.get("sessions", [])
        if isinstance(item, dict)
    ]
    return {"active_id": payload.get("active_id"), "sessions": sorted(sessions, key=lambda s: str(s["id"]))}


def provider_state(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if "provider_keys" not in tables_present(connection):
        return []
    # Only a keyed hash of the ciphertext travels into the fingerprint: the
    # encrypted key itself must never land in an evidence file.
    out = []
    for row in connection.execute(
        "SELECT provider, encrypted_key, base_url FROM provider_keys ORDER BY provider"
    ).fetchall():
        cipher = str(row["encrypted_key"] or "")
        out.append({
            "provider": row["provider"],
            "key_digest": hashlib.sha256(cipher.encode("utf-8")).hexdigest()[:16] if cipher else None,
            "base_url": row["base_url"],
        })
    return out


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def open_readonly_db() -> tuple[Path, sqlite3.Connection]:
    path = db_path()
    return path, connect(path)


def rows_as_dicts(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    with closing(connection.execute(query)) as cursor:
        return [dict(row) for row in cursor.fetchall()]


def emit(payload: Any) -> None:
    """Print a helper result as UTF-8 JSON.

    PowerShell captures stdout with the console code page, which on a Vietnamese
    Windows install is cp1252 and cannot encode the Vietnamese project and stage
    names stored in the database.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
