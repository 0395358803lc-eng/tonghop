"""Point a cloned TH Media data root at its new location.

update_acceptance.ps1 copies the real user data into a sandbox and then installs
over it. Rows and JSON files still name the original absolute root, so the copy
would read through to the user's live media instead of the clone - and the leak
assertion exists to catch exactly that.

Every replacement is attempted for both the spelling the caller passed and the
resolved spelling, because Windows short names (C\\Users\\SOMEON~1) do not match
the long form the database recorded.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_state import emit  # noqa: E402


def spellings(root: Path) -> list[str]:
    values: list[str] = []
    for candidate in (root, Path(str(root)), root.resolve()):
        text = str(candidate)
        if text and text not in values:
            values.append(text)
    return values


def rewrite_database(database: Path, pairs: list[tuple[str, str]]) -> int:
    updates = 0
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            for column in columns:
                name = str(column[1])
                declared = str(column[2] or "").upper()
                if not any(token in declared for token in ("TEXT", "CHAR", "CLOB")):
                    continue
                column_q = '"' + name.replace('"', '""') + '"'
                for old, new in pairs:
                    cursor = connection.execute(
                        f"UPDATE {quoted} SET {column_q}=replace({column_q}, ?, ?) WHERE instr({column_q}, ?) > 0",
                        (old, new, old),
                    )
                    updates += int(cursor.rowcount or 0)
        connection.commit()
    return updates


def rewrite_json(path: Path, pairs: list[tuple[str, str]]) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    rewritten = text
    for old, new in pairs:
        rewritten = rewritten.replace(old.replace("\\", "\\\\"), new.replace("\\", "\\\\"))
        rewritten = rewritten.replace(old, new)
        rewritten = rewritten.replace(old.replace("\\", "/"), new.replace("\\", "/"))
    if rewritten == text:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Rewrite recorded paths from a source data root into a clone.")
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()

    source_root = Path(args.source).expanduser()
    target_root = Path(args.target).expanduser()
    if not source_root.is_dir():
        raise SystemExit(f"REWRITE_SOURCE_MISSING: {source_root}")
    if not target_root.is_dir():
        raise SystemExit(f"REWRITE_TARGET_MISSING: {target_root}")

    target_text = str(target_root.resolve())
    pairs = [(spelling, target_text) for spelling in spellings(source_root)]
    # Longest prefix first so a nested spelling is not half-rewritten by the root.
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)

    database = target_root / "Database" / "aihub.db"
    database_updates = rewrite_database(database, pairs) if database.is_file() else 0
    rewritten_files = [
        str(path.relative_to(target_root))
        for path in [target_root / "flow_bridge_jobs.json", target_root / "Database" / "flow_sessions.json"]
        if rewrite_json(path, pairs)
    ]

    emit({
        "source": str(source_root),
        "target": target_text,
        "source_spellings": [old for old, _ in pairs],
        "database": str(database) if database.is_file() else None,
        "database_replacements": database_updates,
        "json_files_rewritten": rewritten_files,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
