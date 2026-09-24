"""Print digests of user media assets as JSON for the update acceptance gate.

Selected media and final renders are resolved through the database because those
rows carry the absolute paths the application actually serves. Narrator audio and
speaker identity data are walked from the data root, since only files back them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_state import (  # noqa: E402
    canonical_sha256,
    emit,
    data_root,
    digest_file,
    listed_file_records,
    open_readonly_db,
    resolve_recorded_path,
    rows_as_dicts,
    tables_present,
)

ASSET_DIRS = {
    "narrator_tts": Path("Media") / "narrator_tts",
    "speaker_calibration": Path("speaker_calibration"),
    "speaker_embeddings": Path("speaker_embeddings"),
    "film_assets": Path("Media") / "film_assets",
    "final_films": Path("Media") / "final_films",
}


def _media_records(root: Path, rows: list[dict]) -> list[dict]:
    records = []
    for row in rows:
        file_path = resolve_recorded_path(root, row.get("file_path"))
        thumbnail_path = resolve_recorded_path(root, row.get("thumbnail_path"))
        records.append({
            "id": row.get("id"),
            "project_id": row.get("project_id"),
            "file": digest_file(file_path) if file_path else {"path": None, "exists": False},
            "thumbnail": digest_file(thumbnail_path) if thumbnail_path else {"path": None, "exists": False},
        })
    return records


def build() -> dict:
    root = data_root()
    _, connection = open_readonly_db()
    try:
        tables = tables_present(connection)
        selected_rows = (
            rows_as_dicts(connection,
                          "SELECT id, project_id, file_path, thumbnail_path FROM film_generated_media"
                          " WHERE is_selected = 1 ORDER BY project_id, id")
            if "film_generated_media" in tables else []
        )
        final_rows = (
            rows_as_dicts(connection,
                          "SELECT id, project_id, media_id FROM film_final_renders ORDER BY project_id, version, id")
            if "film_final_renders" in tables else []
        )
        media_by_id = {
            str(row["id"]): row
            for row in (rows_as_dicts(connection, "SELECT id, file_path, thumbnail_path FROM film_generated_media")
                        if "film_generated_media" in tables else [])
        }
    finally:
        connection.close()

    selected_media_files = _media_records(root, selected_rows)
    final_files = []
    for row in final_rows:
        source = media_by_id.get(str(row.get("media_id") or ""), {})
        path = resolve_recorded_path(root, source.get("file_path"))
        final_files.append({
            "id": row.get("id"),
            "project_id": row.get("project_id"),
            "media_id": row.get("media_id"),
            "file": digest_file(path) if path else {"path": None, "exists": False},
        })

    assets = {name: listed_file_records(root, str(relative)) for name, relative in ASSET_DIRS.items()}

    return {
        "fingerprint": canonical_sha256({
            "selected_media_files": selected_media_files,
            "final_files": final_files,
            "assets": assets,
        }),
        "selected_media_files": selected_media_files,
        "final_files": final_files,
        "narrator_tts": assets["narrator_tts"],
        "speaker_calibration": assets["speaker_calibration"],
        "speaker_embeddings": assets["speaker_embeddings"],
        "film_assets": assets["film_assets"],
        "final_films": assets["final_films"],
    }


def main() -> int:
    emit(build())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
