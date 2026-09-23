import argparse
import hashlib
import json
import os
from contextlib import closing
import shutil
import sqlite3
import time
from pathlib import Path

COPY_DIRS = [
    "film_assets",
    "final_films",
    "flow_downloads",
    "flow_image_downloads",
    "generated_media",
    "models",
    "narrator_tts",
    "speaker_calibration",
    "speaker_embeddings",
    "video_frames",
]

MEDIA_COPY_DIRS = {
    "film_assets",
    "final_films",
    "flow_downloads",
    "flow_image_downloads",
    "generated_media",
    "narrator_tts",
    "video_frames",
}


def directory_target(name: str) -> Path:
    if name in MEDIA_COPY_DIRS:
        return Path("Media") / name
    if name == "models":
        return Path("Models")
    return Path(name)

RENAMED_DIRS = {
    "flow_chrome_profile": "FlowProfile",
    "flow_sessions": "FlowSessions",
}

COPY_FILES = {
    "flow_bridge_jobs.json": "flow_bridge_jobs.json",
    "flow_sessions.json": "Database/flow_sessions.json",
}

PROFILE_SKIP_DIRS = {
    "Crashpad", "ShaderCache", "GrShaderCache", "Code Cache", "GPUCache",
}
PROFILE_SKIP_FILES = {
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", "RunningChromeVersion", "DevToolsActivePort",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: Path, *, profile: bool = False):
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        if profile:
            dirs[:] = [name for name in dirs if name not in PROFILE_SKIP_DIRS]
        base = Path(current)
        for name in files:
            if profile and (name in PROFILE_SKIP_FILES or name.endswith(".lock")):
                continue
            source = base / name
            if source.is_file():
                yield source


def tree_stats(root: Path, *, profile: bool = False) -> tuple[int, int]:
    count = 0
    size = 0
    for path in iter_files(root, profile=profile) or []:
        count += 1
        size += path.stat().st_size
    return count, size
def copy_file_verified(source: Path, target: Path, *, verify_hash: bool = False) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    source_size = source.stat().st_size
    target_size = target.stat().st_size
    if source_size != target_size:
        raise RuntimeError(f"Size mismatch after copy: {source} -> {target}")

    result = {
        "source": str(source),
        "target": str(target),
        "bytes": source_size,
    }
    if verify_hash:
        source_hash = sha256_file(source)
        target_hash = sha256_file(target)
        if source_hash != target_hash:
            raise RuntimeError(f"Hash mismatch after copy: {source} -> {target}")
        result["sha256"] = source_hash
    return result


def copy_tree_verified(source: Path, target: Path, *, profile: bool = False) -> tuple[int, int]:
    copied = 0
    copied_bytes = 0
    for item in iter_files(source, profile=profile) or []:
        relative = item.relative_to(source)
        output = target / relative
        record = copy_file_verified(item, output, verify_hash=False)
        copied += 1
        copied_bytes += int(record["bytes"])
    return copied, copied_bytes


def backup_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
def sqlite_integrity(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "")


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def migration_path_replacements(source_root: Path, target_root: Path) -> list[tuple[Path, Path]]:
    replacements = [
        (source_root / "flow_chrome_profile", target_root / "FlowProfile"),
        (source_root / "flow_sessions", target_root / "FlowSessions"),
    ]
    for name in COPY_DIRS:
        replacements.append((source_root / name, target_root / directory_target(name)))
    replacements.append((source_root, target_root))
    return replacements


def rewrite_database_paths(db_path: Path, source_root: Path, target_root: Path) -> int:
    replacements = migration_path_replacements(source_root, target_root)
    pairs: list[tuple[str, str]] = []
    for old, new in replacements:
        old_s = str(old)
        new_s = str(new)
        pairs.append((old_s, new_s))
        pairs.append((old_s.replace("\\", "\\\\"), new_s.replace("\\", "\\\\")))
        pairs.append((old_s.replace("\\", "/"), new_s.replace("\\", "/")))

    updates = 0
    with closing(sqlite3.connect(db_path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
            for column in columns:
                name = str(column[1])
                declared = str(column[2] or "").upper()
                if declared and not any(token in declared for token in ("TEXT", "CHAR", "CLOB")):
                    continue
                table_q = _quoted(table)
                column_q = _quoted(name)
                for old, new in pairs:
                    if not old or old == new:
                        continue
                    cursor = conn.execute(
                        f"UPDATE {table_q} SET {column_q}=replace({column_q}, ?, ?) "
                        f"WHERE instr({column_q}, ?) > 0",
                        (old, new, old),
                    )
                    if cursor.rowcount and cursor.rowcount > 0:
                        updates += int(cursor.rowcount)
        conn.commit()
    return updates


def build_plan(source_root: Path) -> dict:
    entries = []
    total_files = 0
    total_bytes = 0

    for name in COPY_DIRS:
        source = source_root / name
        count, size = tree_stats(source)
        if source.exists():
            entries.append({"source": name, "target": str(directory_target(name)).replace("\\", "/"), "files": count, "bytes": size})
            total_files += count
            total_bytes += size

    for old_name, new_name in RENAMED_DIRS.items():
        source = source_root / old_name
        count, size = tree_stats(source, profile=(old_name == "flow_chrome_profile"))
        if source.exists():
            entries.append({"source": old_name, "target": new_name, "files": count, "bytes": size})
            total_files += count
            total_bytes += size

    for old_name, new_name in COPY_FILES.items():
        source = source_root / old_name
        if source.exists():
            size = source.stat().st_size
            entries.append({"source": old_name, "target": new_name, "files": 1, "bytes": size})
            total_files += 1
            total_bytes += size
    for old_name, new_name in (
        ("aihub.db", "Database/aihub.db"),
        ("master.key", "Database/master.key"),
        ("master.key.dpapi", "Database/master.key.dpapi"),
    ):
        source = source_root / old_name
        if source.exists():
            size = source.stat().st_size
            entries.append({"source": old_name, "target": new_name, "files": 1, "bytes": size})
            total_files += 1
            total_bytes += size

    backups = list(source_root.glob("backup*.db"))
    backup_bytes = sum(path.stat().st_size for path in backups if path.is_file())
    if backups:
        entries.append({
            "source": "backup*.db",
            "target": "Backups/",
            "files": len(backups),
            "bytes": backup_bytes,
        })
        total_files += len(backups)
        total_bytes += backup_bytes

    return {
        "source": str(source_root),
        "entries": entries,
        "total_files": total_files,
        "total_bytes": total_bytes,
    }


def _stage_path(target_root: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return target_root.parent / f"{target_root.name}.migration-staging-{stamp}"


def migrate(source_root: Path, target_root: Path, *, replace_target: bool = False) -> dict:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if not (source_root / "aihub.db").exists():
        raise RuntimeError(f"Không tìm thấy aihub.db tại {source_root}")
    if not any((source_root / name).exists() for name in ("master.key", "master.key.dpapi")):
        raise RuntimeError(f"Không tìm thấy master.key hoặc master.key.dpapi tại {source_root}")
    if target_root.exists() and any(target_root.iterdir()) and not replace_target:
        raise RuntimeError(f"Target đã có dữ liệu: {target_root}")
    stage = _stage_path(target_root)
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)

    for name in (
        "Database", "Media", "Models", "TTS", "FlowProfile",
        "FlowSessions", "Logs", "Backups", "Temp",
    ):
        (stage / name).mkdir(parents=True, exist_ok=True)

    copied_files = 0
    copied_bytes = 0
    previous_target = None
    activated = False
    try:
        target_db = stage / "Database" / "aihub.db"
        backup_sqlite(source_root / "aihub.db", target_db)
        db_snapshot = stage / "Backups" / "pre_path_rewrite_aihub.db"
        shutil.copy2(target_db, db_snapshot)

        key_records = {}
        for key_name in ("master.key", "master.key.dpapi"):
            source_key = source_root / key_name
            if not source_key.is_file():
                continue
            record = copy_file_verified(
                source_key,
                stage / "Database" / key_name,
                verify_hash=True,
            )
            key_records[key_name] = record
            copied_files += 1
            copied_bytes += int(record["bytes"])
        key_record = key_records.get("master.key") or key_records.get("master.key.dpapi")
        if not key_record:
            raise RuntimeError("Không thể sao chép khóa mã hóa legacy.")

        for name in COPY_DIRS:
            source = source_root / name
            if not source.exists():
                continue
            count, size = copy_tree_verified(source, stage / directory_target(name))
            copied_files += count
            copied_bytes += size

        for old_name, new_name in RENAMED_DIRS.items():
            source = source_root / old_name
            if not source.exists():
                continue
            count, size = copy_tree_verified(
                source,
                stage / new_name,
                profile=(old_name == "flow_chrome_profile"),
            )
            copied_files += count
            copied_bytes += size
        for old_name, new_name in COPY_FILES.items():
            source = source_root / old_name
            if not source.exists():
                continue
            record = copy_file_verified(source, stage / new_name, verify_hash=False)
            copied_files += 1
            copied_bytes += int(record["bytes"])

        for backup in source_root.glob("backup*.db"):
            if not backup.is_file():
                continue
            record = copy_file_verified(
                backup,
                stage / "Backups" / backup.name,
                verify_hash=False,
            )
            copied_files += 1
            copied_bytes += int(record["bytes"])

        text_replacements = [
            (str(old), str(new))
            for old, new in migration_path_replacements(source_root, target_root)
        ]
        for json_path in (
            stage / "flow_bridge_jobs.json",
            stage / "Database" / "flow_sessions.json",
        ):
            if not json_path.exists():
                continue
            text = json_path.read_text(encoding="utf-8")
            for old, new in text_replacements:
                text = text.replace(old, new)
                text = text.replace(
                    old.replace("\\", "\\\\"),
                    new.replace("\\", "\\\\"),
                )
                text = text.replace(old.replace("\\", "/"), new.replace("\\", "/"))
            json_path.write_text(text, encoding="utf-8")

        path_updates = rewrite_database_paths(target_db, source_root, target_root)
        integrity = sqlite_integrity(target_db)
        if integrity.lower() != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        manifest = {
            "version": 1,
            "created_at_epoch": time.time(),
            "source": str(source_root),
            "target": str(target_root),
            "copied_files": copied_files,
            "copied_bytes": copied_bytes,
            "database_integrity": integrity,
            "database_path_updates": path_updates,
            "master_key_sha256": key_record["sha256"],
            "excluded": [
                "flow_bridge_config.json",
                "*.log",
                "diagnostic/test artifacts",
                "Chrome cache/lock files",
            ],
        }
        (stage / "migration_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if target_root.exists():
            if any(target_root.iterdir()):
                if not replace_target:
                    raise RuntimeError(f"Target đã có dữ liệu: {target_root}")
                stamp = time.strftime("%Y%m%d_%H%M%S")
                previous_target = target_root.parent / f"{target_root.name}.previous-{stamp}"
                target_root.replace(previous_target)
            else:
                target_root.rmdir()

        stage.replace(target_root)
        activated = True
        manifest["previous_target_backup"] = str(previous_target) if previous_target else None
        (target_root / "migration_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

        failed_target = None
        if activated and target_root.exists():
            stamp = time.strftime("%Y%m%d_%H%M%S")
            failed_target = target_root.parent / f"{target_root.name}.failed-{stamp}"
            target_root.replace(failed_target)

        if previous_target is not None and previous_target.exists():
            if target_root.exists():
                shutil.rmtree(target_root, ignore_errors=True)
            previous_target.replace(target_root)

        raise


def default_target() -> Path:
    local = os.getenv("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA không tồn tại.")
    return Path(local) / "TH Media" / "Desktop"
def main() -> None:
    parser = argparse.ArgumentParser(description="TH Media legacy .data migration")
    parser.add_argument("--source", required=True, help="Legacy .data directory")
    parser.add_argument("--target", help="Desktop data directory; default: %LOCALAPPDATA%\\TH Media\\Desktop")
    parser.add_argument("--execute", action="store_true", help="Perform migration; otherwise dry-run")
    parser.add_argument(
        "--replace-target",
        action="store_true",
        help="Move an existing target aside before activating migrated data",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    target = Path(args.target).expanduser() if args.target else default_target()
    plan = build_plan(source)
    plan["target"] = str(target.resolve())
    plan["mode"] = "execute" if args.execute else "dry-run"

    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    result = migrate(source, target, replace_target=args.replace_target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
