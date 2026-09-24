"""Print the durable TH Media desktop state as JSON for restart/update acceptance.

Volatile state is excluded on purpose: updated_at/created_at columns, the values
in secure_settings (the desktop rotates a runtime token) and browser cache noise.
That keeps the fingerprint comparable across an application restart and across
an installer upgrade, which is exactly what the acceptance gates assert.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_state import (  # noqa: E402
    canonical_sha256,
    emit,
    data_root,
    flow_profile_has_state,
    flow_session_state,
    open_readonly_db,
    provider_state,
    rows_as_dicts,
    tables_present,
)

SELECTED_MEDIA_COLUMNS = (
    "id", "project_id", "resource_type", "media_type", "role", "status",
    "file_path", "thumbnail_path", "file_size", "qc_status",
)
PROJECT_COLUMNS = ("id", "name", "provider", "model", "status", "stage", "progress")
MEDIA_ORDER = "project_id, id"


def _select(connection, table: str, columns: tuple[str, ...], where: str = "") -> list[dict]:
    present = tables_present(connection)
    if table not in present:
        return []
    available = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
    chosen = [column for column in columns if column in available]
    if not chosen:
        return []
    query = f'SELECT {", ".join(chosen)} FROM "{table}"'
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY " + (MEDIA_ORDER if table == "film_generated_media" else "rowid")
    return rows_as_dicts(connection, query)


def build() -> dict:
    root = data_root()
    database, connection = open_readonly_db()
    try:
        projects = _select(connection, "film_projects", PROJECT_COLUMNS)
        selected_media = _select(connection, "film_generated_media", SELECTED_MEDIA_COLUMNS, "is_selected = 1")
        render_jobs = _select(connection, "film_render_jobs", ("id", "project_id", "scene_index", "status"))
        runs = _select(connection, "film_pipeline_runs", ("id", "project_id", "status"))
        finals = _select(connection, "film_final_renders", ("id", "project_id", "version", "status", "manifest_hash"))
        queues = _select(connection, "film_render_queues", ("project_id", "adapter", "paused"))
        providers = provider_state(connection)
        setting_keys = (
            sorted(str(row[0]) for row in connection.execute("SELECT key FROM secure_settings"))
            if "secure_settings" in tables_present(connection) else []
        )
    finally:
        connection.close()

    flow_sessions = flow_session_state(root)
    durable = {
        "projects": projects,
        "selected_media": selected_media,
        "render_jobs": render_jobs,
        "runs": runs,
        "finals": finals,
        "queues": queues,
        "providers": providers,
        "secure_setting_keys": setting_keys,
        "flow_sessions": flow_sessions,
    }
    return {
        "database": str(database),
        "fingerprint": canonical_sha256(durable),
        "projects": projects,
        "selected_media": selected_media,
        "render_jobs": render_jobs,
        "runs": runs,
        "finals": finals,
        "queues": queues,
        "providers": providers,
        "secure_setting_keys": setting_keys,
        "flow_sessions": flow_sessions,
        "flow_profile_nonempty": flow_profile_has_state(root),
    }


def main() -> int:
    emit(build())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
