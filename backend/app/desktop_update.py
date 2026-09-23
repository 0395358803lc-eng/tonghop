from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, DB_PATH
from .film_scene_state_store import list_active_runs

BACKUP_ROOT = DATA_DIR / "Backups"


def update_gate_status() -> dict:
    active = list_active_runs()
    return {
        "ok": True,
        "can_update": len(active) == 0,
        "active_pipelines": len(active),
        "active_runs": [
            {
                "project_id": item.get("project_id"),
                "run_id": item.get("run_id"),
                "status": item.get("status"),
                "current_scene_id": item.get("current_scene_id"),
            }
            for item in active
        ],
    }
def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _copy_optional(source: Path, destination: Path) -> str | None:
    if not source.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def _user_backup_keep() -> int:
    settings = DATA_DIR / "desktop-settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
        value = int(payload.get("backup_keep", 5))
        return max(1, min(value, 30))
    except Exception:
        return 5


def _prune_user_backups() -> None:
    keep = _user_backup_keep()
    folders = sorted(
        (item for item in BACKUP_ROOT.glob("user-*") if item.is_dir()),
        key=lambda item: item.name,
        reverse=True,
    )
    for folder in folders[keep:]:
        shutil.rmtree(folder, ignore_errors=True)


def create_desktop_backup(kind: str = "user") -> dict:
    gate = update_gate_status()
    if not gate["can_update"]:
        return {**gate, "prepared": False, "reason": "pipeline_active"}

    stamp = _timestamp()
    folder = BACKUP_ROOT / f"{kind}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    db_backup = folder / DB_PATH.name
    if DB_PATH.is_file():
        _backup_sqlite(DB_PATH, db_backup)

    copied = {}
    for source, name in (
        (DATA_DIR / "desktop-settings.json", "desktop-settings.json"),
        (DATA_DIR / "flow_bridge_config.json", "flow_bridge_config.json"),
        (DB_PATH.parent / "flow_sessions.json", "flow_sessions.json"),
        (DB_PATH.parent / "master.key.dpapi", "master.key.dpapi"),
        (DB_PATH.parent / "master.key", "master.key"),
    ):
        saved = _copy_optional(source, folder / name)
        if saved:
            copied[name] = saved

    manifest = {
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(DB_PATH),
        "database_backup": str(db_backup) if db_backup.is_file() else None,
        "copied": copied,
        "gate": gate,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if kind == "user":
        _prune_user_backups()
    return {**gate, "prepared": True, "backup_name": folder.name, "backup_dir": str(folder), "database_backup": manifest["database_backup"], "backup_keep": _user_backup_keep()}


def list_desktop_backups() -> dict:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for folder in sorted((item for item in BACKUP_ROOT.iterdir() if item.is_dir()), reverse=True):
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "name": folder.name,
            "kind": manifest.get("kind") or ("pre-update" if folder.name.startswith("pre-update-") else "unknown"),
            "created_at": manifest.get("created_at"),
            "database": (folder / DB_PATH.name).is_file(),
            "settings": (folder / "desktop-settings.json").is_file(),
        })
    return {"ok": True, "backups": items}


def stage_desktop_restore(backup_name: str) -> dict:
    gate = update_gate_status()
    if not gate["can_update"]:
        return {**gate, "staged": False, "reason": "pipeline_active"}
    safe_name = Path(str(backup_name or "")).name
    if safe_name != backup_name or not safe_name:
        raise ValueError("BACKUP_NAME_INVALID")
    folder = (BACKUP_ROOT / safe_name).resolve()
    if folder.parent != BACKUP_ROOT.resolve() or not folder.is_dir():
        raise ValueError("BACKUP_NOT_FOUND")
    if not (folder / DB_PATH.name).is_file():
        raise ValueError("BACKUP_DATABASE_MISSING")
    marker = DATA_DIR / "pending-restore.json"
    marker.write_text(json.dumps({"backup_name": safe_name, "backup_dir": str(folder)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**gate, "staged": True, "backup_name": safe_name, "marker": str(marker)}


def prepare_update_backup() -> dict:
    gate = update_gate_status()
    if not gate["can_update"]:
        return {**gate, "prepared": False, "reason": "pipeline_active"}

    stamp = _timestamp()
    folder = BACKUP_ROOT / f"pre-update-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    db_backup = folder / DB_PATH.name

    if DB_PATH.is_file():
        _backup_sqlite(DB_PATH, db_backup)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(DB_PATH),
        "database_backup": str(db_backup) if db_backup.is_file() else None,
        "gate": gate,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Preserve the wrapped encryption key when present so an update backup
    # remains useful together with the database.
    for key_name in ("master.key.dpapi", "master.key"):
        source = DB_PATH.parent / key_name
        if source.is_file():
            shutil.copy2(source, folder / key_name)

    return {
        **gate,
        "prepared": True,
        "backup_dir": str(folder),
        "database_backup": str(db_backup) if db_backup.is_file() else None,
    }
