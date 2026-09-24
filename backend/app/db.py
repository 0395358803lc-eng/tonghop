import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS provider_keys (
 provider TEXT PRIMARY KEY, encrypted_key TEXT NOT NULL, base_url TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS chats (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS chat_state (
 chat_id TEXT PRIMARY KEY, data TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS video_jobs (
 id TEXT PRIMARY KEY, url TEXT NOT NULL, platform TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'Đang xếp hàng', progress INTEGER NOT NULL DEFAULT 0,
 title TEXT, thumbnail TEXT, duration REAL, channel TEXT, transcript_source TEXT, transcript TEXT, report TEXT, error TEXT,
 vision_status TEXT DEFAULT 'pending', vision_model TEXT, vision_note TEXT, visual_summary TEXT, keyframes_json TEXT DEFAULT '[]',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS secure_settings (
 key TEXT PRIMARY KEY, encrypted_value TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS film_projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, original_text TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', stage TEXT NOT NULL DEFAULT 'Chờ phân tích', progress INTEGER NOT NULL DEFAULT 0,
 settings_json TEXT NOT NULL DEFAULT '{}', master_prompt TEXT, story_bible_json TEXT DEFAULT '{}',
 character_bible_json TEXT DEFAULT '[]', location_bible_json TEXT DEFAULT '[]', prop_bible_json TEXT DEFAULT '[]',
 visual_style TEXT, timeline_json TEXT DEFAULT '[]', source_manifest_json TEXT DEFAULT '{}', integrity_json TEXT DEFAULT '{}', production_gate_json TEXT DEFAULT '{}', consistency_report_json TEXT DEFAULT '{}', repair_log_json TEXT DEFAULT '[]', error TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS film_scenes (
 row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, project_id TEXT NOT NULL, scene_index INTEGER NOT NULL, title TEXT, source_text TEXT, summary TEXT,
 duration REAL NOT NULL DEFAULT 8, characters_json TEXT DEFAULT '[]', location_id TEXT, action TEXT, camera TEXT,
 lighting TEXT, atmosphere TEXT, voiceover TEXT, dialogue_json TEXT DEFAULT '[]', start_state TEXT, end_state TEXT,
 continuity_json TEXT DEFAULT '{}', visual_prompt TEXT, flow_prompt TEXT, warnings_json TEXT DEFAULT '[]',
 source_hash TEXT, source_snapshot_json TEXT DEFAULT '{}', props_present_json TEXT DEFAULT '[]', prop_transfers_json TEXT DEFAULT '[]',
 start_state_json TEXT DEFAULT '{}', end_state_json TEXT DEFAULT '{}', gate_json TEXT DEFAULT '{}',
 source_span_json TEXT DEFAULT '{}', duration_budget_json TEXT DEFAULT '{}', shots_json TEXT DEFAULT '[]',
 flow_prompt_meta_json TEXT DEFAULT '{}', merge_audit_json TEXT DEFAULT '{}',
 render_status TEXT NOT NULL DEFAULT 'not_configured', result_url TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(project_id,id), UNIQUE(project_id,scene_index),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_scenes_project ON film_scenes(project_id, scene_index);
CREATE TABLE IF NOT EXISTS film_render_queues (
 project_id TEXT PRIMARY KEY, adapter TEXT NOT NULL DEFAULT 'none', paused INTEGER NOT NULL DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS film_render_jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL, scene_index INTEGER NOT NULL,
 adapter TEXT NOT NULL DEFAULT 'none', status TEXT NOT NULL DEFAULT 'waiting', progress INTEGER NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0, prompt TEXT NOT NULL DEFAULT '', reference_json TEXT NOT NULL DEFAULT '{}',
 provider_job_id TEXT, result_url TEXT, first_frame_url TEXT, last_frame_url TEXT, error TEXT, provider_error_code TEXT,
 qc_status TEXT NOT NULL DEFAULT 'not_run', consistency_score REAL, qc_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,scene_id) REFERENCES film_scenes(project_id,id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_render_jobs_project ON film_render_jobs(project_id, scene_index, created_at);
CREATE INDEX IF NOT EXISTS idx_film_render_jobs_scene ON film_render_jobs(project_id, scene_id, created_at);
CREATE TABLE IF NOT EXISTS film_provider_resources (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, provider TEXT NOT NULL, resource_type TEXT NOT NULL,
 entity_id TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 provider_ref TEXT, local_path TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', error TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_film_provider_resources_entity
 ON film_provider_resources(project_id,provider,resource_type,entity_id);
CREATE INDEX IF NOT EXISTS idx_film_provider_resources_status
 ON film_provider_resources(project_id,provider,status);
CREATE TABLE IF NOT EXISTS film_generated_media (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT, resource_type TEXT, entity_id TEXT,
 output_key TEXT NOT NULL, media_type TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 provider TEXT, model TEXT, provider_job_id TEXT, file_path TEXT, thumbnail_path TEXT, mime_type TEXT,
 file_size INTEGER, width INTEGER, height INTEGER, duration_seconds REAL,
 version INTEGER NOT NULL DEFAULT 1, is_selected INTEGER NOT NULL DEFAULT 0,
 qc_status TEXT NOT NULL DEFAULT 'not_run', qc_score REAL, qc_json TEXT NOT NULL DEFAULT '{}',
 metadata_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_media_project ON film_generated_media(project_id, role, status, created_at);
CREATE INDEX IF NOT EXISTS idx_film_media_scene ON film_generated_media(project_id, scene_id, created_at);
CREATE INDEX IF NOT EXISTS idx_film_media_output ON film_generated_media(project_id, output_key, version);
CREATE UNIQUE INDEX IF NOT EXISTS idx_film_media_provider_job ON film_generated_media(provider_job_id) WHERE provider_job_id IS NOT NULL AND provider_job_id != '';
"""

PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS film_pipeline_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle',
 current_scene_id TEXT, current_scene_index INTEGER, stop_after_current INTEGER NOT NULL DEFAULT 0,
 from_scene_id TEXT, scene_limit INTEGER, gate_json TEXT NOT NULL DEFAULT '{}', log_json TEXT NOT NULL DEFAULT '[]',
 error TEXT, started_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_pipeline_runs_project ON film_pipeline_runs(project_id, created_at);
CREATE TABLE IF NOT EXISTS film_scene_pipeline (
 project_id TEXT NOT NULL, scene_id TEXT NOT NULL, scene_index INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'LOCKED', attempt INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 2,
 current_run_id TEXT, current_job_id TEXT, selected_media_id TEXT, best_media_id TEXT, best_score REAL,
 best_rank_json TEXT NOT NULL DEFAULT '{}', error TEXT, blocked_reason TEXT,
 qc_json TEXT NOT NULL DEFAULT '{}', repair_json TEXT NOT NULL DEFAULT '[]', snapshot_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(project_id, scene_id),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS film_scene_ledger (
 project_id TEXT NOT NULL, scene_id TEXT NOT NULL, selected_media_id TEXT,
 accepted_first_frame TEXT, accepted_last_frame TEXT,
 character_positions_json TEXT NOT NULL DEFAULT '{}', character_pose TEXT, character_wardrobe TEXT,
 character_visibility_json TEXT NOT NULL DEFAULT '{}', prop_owner_json TEXT NOT NULL DEFAULT '{}',
 prop_holder_json TEXT NOT NULL DEFAULT '{}', prop_location_json TEXT NOT NULL DEFAULT '{}',
 prop_state_json TEXT NOT NULL DEFAULT '{}', location_id TEXT, time_of_day TEXT, lighting_state TEXT,
 camera_direction TEXT, dialogue_state TEXT, audio_state TEXT, snapshot_json TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(project_id, scene_id),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS film_pipeline_candidates (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL, run_id TEXT, job_id TEXT, media_id TEXT,
 attempt INTEGER NOT NULL DEFAULT 0, hard_gates_passed INTEGER NOT NULL DEFAULT 0, dimensions_passed INTEGER NOT NULL DEFAULT 0,
 overall_score REAL, qc_json TEXT NOT NULL DEFAULT '{}', is_best INTEGER NOT NULL DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_pipeline_candidates_scene ON film_pipeline_candidates(project_id, scene_id, created_at);
CREATE TABLE IF NOT EXISTS film_pipeline_execution_leases (
 project_id TEXT PRIMARY KEY, scene_id TEXT, run_id TEXT, worker_id TEXT NOT NULL,
 lease_until TEXT NOT NULL, updated_at TEXT,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
"""

BATCH3_SCHEMA = """
CREATE TABLE IF NOT EXISTS film_voice_profiles (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, character_id TEXT NOT NULL,
 profile_json TEXT NOT NULL DEFAULT '{}', provider TEXT, provider_voice_id TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
 UNIQUE(project_id, character_id),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS film_scene_audio_requirements (
 project_id TEXT NOT NULL, scene_id TEXT NOT NULL,
 requirements_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT,
 PRIMARY KEY(project_id, scene_id),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS film_scene_junctions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
 previous_scene_id TEXT NOT NULL, next_scene_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING',
 selected_previous_media_id TEXT, selected_next_media_id TEXT,
 qc_json TEXT NOT NULL DEFAULT '{}', score REAL, attempt INTEGER NOT NULL DEFAULT 0, error TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
 UNIQUE(project_id, previous_scene_id, next_scene_id),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_junctions_project ON film_scene_junctions(project_id, previous_scene_id);
CREATE TABLE IF NOT EXISTS film_final_renders (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL DEFAULT 'ASSEMBLY_PENDING', media_id TEXT,
 manifest_json TEXT NOT NULL DEFAULT '{}', manifest_hash TEXT, error TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT,
 UNIQUE(project_id, version),
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_final_project ON film_final_renders(project_id, version DESC);
CREATE TABLE IF NOT EXISTS film_acceptance_snapshots (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_acceptance_scene ON film_acceptance_snapshots(project_id, scene_id, created_at);
"""

BATCH4_SCHEMA = """
CREATE TABLE IF NOT EXISTS film_pipeline_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT, scene_id TEXT, job_id TEXT,
 event_type TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'INFO',
 payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_film_events_project ON film_pipeline_events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_film_events_run ON film_pipeline_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_film_events_scene ON film_pipeline_events(project_id, scene_id, created_at);
CREATE INDEX IF NOT EXISTS idx_film_events_type ON film_pipeline_events(event_type);
CREATE TABLE IF NOT EXISTS film_capability_matrix (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, media_type TEXT NOT NULL,
 max_references INTEGER, resolutions_json TEXT NOT NULL DEFAULT '[]',
 durations_json TEXT NOT NULL DEFAULT '[]', aspect_ratios_json TEXT NOT NULL DEFAULT '[]',
 supports_image_reference INTEGER NOT NULL DEFAULT 1, supports_audio INTEGER NOT NULL DEFAULT 1,
 raw_json TEXT NOT NULL DEFAULT '{}', checked_at TEXT NOT NULL,
 UNIQUE(provider, model, media_type));
CREATE TABLE IF NOT EXISTS film_idempotency_keys (
 key TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation TEXT NOT NULL,
 result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, expires_at TEXT);
"""

ISOLATION_ENV = "TH_MEDIA_REQUIRE_DB_ISOLATION"
ISOLATED_DB_ENV = "TH_MEDIA_ISOLATED_DB"


def production_db_paths() -> set[str]:
    """Stores that hold real data: the dev scratch tree and the installed app tree."""
    candidates = [config.SOURCE_ROOT / ".data" / "aihub.db"]
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "TH Media" / "Desktop" / "Database" / "aihub.db")
    resolved = set()
    for path in candidates:
        try:
            resolved.add(os.path.normcase(str(path.resolve())))
        except OSError:  # pragma: no cover - unreadable root
            resolved.add(os.path.normcase(str(path)))
    return resolved


def _guard_isolation(path) -> None:
    """Fail closed: a gated run may not open a store that holds real data.

    The suite isolates two ways - the rebind harness, and a subprocess handed
    TH_MEDIA_DB_PATH - so the question is "is this a production path", not "did you use
    my helper". config resolves its paths at import time, so without this guard a test
    that forgets to isolate appends rows to the acceptance store and still reports OK.
    """
    if os.getenv(ISOLATION_ENV) != "1":
        return
    try:
        target = os.path.normcase(str(Path(str(path)).resolve()))
    except OSError:
        return
    if target in production_db_paths():
        raise RuntimeError(f"DB_ISOLATION_REQUIRED: refusing to open production store {path}")


@contextmanager
def connect():
    _guard_isolation(config.DB_PATH)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

FILM_PROJECT_MIGRATIONS = {
    "source_manifest_json": "TEXT DEFAULT '{}'",
    "integrity_json": "TEXT DEFAULT '{}'",
    "production_gate_json": "TEXT DEFAULT '{}'",
    "consistency_report_json": "TEXT DEFAULT '{}'",
    "repair_log_json": "TEXT DEFAULT '[]'",
}

FILM_SCENE_MIGRATIONS = {
    "source_hash": "TEXT",
    "source_snapshot_json": "TEXT DEFAULT '{}'",
    "props_present_json": "TEXT DEFAULT '[]'",
    "prop_transfers_json": "TEXT DEFAULT '[]'",
    "start_state_json": "TEXT DEFAULT '{}'",
    "end_state_json": "TEXT DEFAULT '{}'",
    "gate_json": "TEXT DEFAULT '{}'",
    "source_span_json": "TEXT DEFAULT '{}'",
    "duration_budget_json": "TEXT DEFAULT '{}'",
    "shots_json": "TEXT DEFAULT '[]'",
    "flow_prompt_meta_json": "TEXT DEFAULT '{}'",
    "merge_audit_json": "TEXT DEFAULT '{}'",
}

FILM_RENDER_JOB_MIGRATIONS = {
    "provider_error_code": "TEXT",
    "media_id": "TEXT",
}

FILM_RESOURCE_MIGRATIONS = {
    "media_id": "TEXT",
}



def _migrate_film_scene_composite_key(conn):
    scene_info = conn.execute("PRAGMA table_info(film_scenes)").fetchall()
    if not scene_info:
        return
    has_row_id = any(row["name"] == "row_id" for row in scene_info)
    id_is_global_pk = any(row["name"] == "id" and int(row["pk"] or 0) > 0 for row in scene_info)
    if has_row_id and not id_is_global_pk:
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE film_render_jobs RENAME TO film_render_jobs_legacy")
        conn.execute("ALTER TABLE film_scenes RENAME TO film_scenes_legacy")

        conn.execute("""
        CREATE TABLE film_scenes (
         row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, project_id TEXT NOT NULL,
         scene_index INTEGER NOT NULL, title TEXT, source_text TEXT, summary TEXT,
         duration REAL NOT NULL DEFAULT 8, characters_json TEXT DEFAULT '[]', location_id TEXT, action TEXT, camera TEXT,
         lighting TEXT, atmosphere TEXT, voiceover TEXT, dialogue_json TEXT DEFAULT '[]', start_state TEXT, end_state TEXT,
         continuity_json TEXT DEFAULT '{}', visual_prompt TEXT, flow_prompt TEXT, warnings_json TEXT DEFAULT '[]',
         source_hash TEXT, source_snapshot_json TEXT DEFAULT '{}', props_present_json TEXT DEFAULT '[]', prop_transfers_json TEXT DEFAULT '[]',
         start_state_json TEXT DEFAULT '{}', end_state_json TEXT DEFAULT '{}', gate_json TEXT DEFAULT '{}',
         source_span_json TEXT DEFAULT '{}', duration_budget_json TEXT DEFAULT '{}', shots_json TEXT DEFAULT '[]',
         flow_prompt_meta_json TEXT DEFAULT '{}', merge_audit_json TEXT DEFAULT '{}',
         render_status TEXT NOT NULL DEFAULT 'not_configured', result_url TEXT,
         created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
         UNIQUE(project_id,id), UNIQUE(project_id,scene_index),
         FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE
        )
        """)

        new_scene_cols = [row["name"] for row in conn.execute("PRAGMA table_info(film_scenes)").fetchall() if row["name"] != "row_id"]
        old_scene_cols = {row["name"] for row in conn.execute("PRAGMA table_info(film_scenes_legacy)").fetchall()}
        scene_copy_cols = [name for name in new_scene_cols if name in old_scene_cols]
        cols = ",".join(scene_copy_cols)
        conn.execute(f"INSERT INTO film_scenes({cols}) SELECT {cols} FROM film_scenes_legacy")

        conn.execute("""
        CREATE TABLE film_render_jobs (
         id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_id TEXT NOT NULL, scene_index INTEGER NOT NULL,
         adapter TEXT NOT NULL DEFAULT 'none', status TEXT NOT NULL DEFAULT 'waiting', progress INTEGER NOT NULL DEFAULT 0,
         attempt INTEGER NOT NULL DEFAULT 0, prompt TEXT NOT NULL DEFAULT '', reference_json TEXT NOT NULL DEFAULT '{}',
         provider_job_id TEXT, result_url TEXT, first_frame_url TEXT, last_frame_url TEXT, error TEXT, provider_error_code TEXT,
         qc_status TEXT NOT NULL DEFAULT 'not_run', consistency_score REAL, qc_json TEXT NOT NULL DEFAULT '{}',
         created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
         FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE,
         FOREIGN KEY(project_id,scene_id) REFERENCES film_scenes(project_id,id) ON DELETE CASCADE
        )
        """)

        new_render_cols = [row["name"] for row in conn.execute("PRAGMA table_info(film_render_jobs)").fetchall()]
        old_render_cols = {row["name"] for row in conn.execute("PRAGMA table_info(film_render_jobs_legacy)").fetchall()}
        render_copy_cols = [name for name in new_render_cols if name in old_render_cols]
        cols = ",".join(render_copy_cols)
        conn.execute(f"INSERT INTO film_render_jobs({cols}) SELECT {cols} FROM film_render_jobs_legacy")

        conn.execute("DROP TABLE film_render_jobs_legacy")
        conn.execute("DROP TABLE film_scenes_legacy")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_film_scenes_project ON film_scenes(project_id, scene_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_film_render_jobs_project ON film_render_jobs(project_id, scene_index, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_film_render_jobs_scene ON film_render_jobs(project_id, scene_id, created_at)")
        conn.commit()

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Film scene key migration produced foreign-key violations: {violations[:5]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


VIDEO_JOB_MIGRATIONS = {
    "vision_status": "TEXT DEFAULT 'pending'",
    "vision_model": "TEXT",
    "vision_note": "TEXT",
    "visual_summary": "TEXT",
    "keyframes_json": "TEXT DEFAULT '[]'",
}

def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(PIPELINE_SCHEMA)
        conn.executescript(BATCH3_SCHEMA)
        conn.executescript(BATCH4_SCHEMA)
        lease_cols = {row["name"] for row in conn.execute("PRAGMA table_info(film_pipeline_execution_leases)").fetchall()}
        if "heartbeat_at" not in lease_cols:
            conn.execute("ALTER TABLE film_pipeline_execution_leases ADD COLUMN heartbeat_at TEXT")
        final_existing = {row["name"] for row in conn.execute("PRAGMA table_info(film_final_renders)").fetchall()}
        if "qc_json" not in final_existing:
            conn.execute("ALTER TABLE film_final_renders ADD COLUMN qc_json TEXT NOT NULL DEFAULT '{}'")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(video_jobs)").fetchall()}
        for column, definition in VIDEO_JOB_MIGRATIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE video_jobs ADD COLUMN {column} {definition}")
        project_existing = {row["name"] for row in conn.execute("PRAGMA table_info(film_projects)").fetchall()}
        for column, definition in FILM_PROJECT_MIGRATIONS.items():
            if column not in project_existing:
                conn.execute(f"ALTER TABLE film_projects ADD COLUMN {column} {definition}")
        scene_existing = {row["name"] for row in conn.execute("PRAGMA table_info(film_scenes)").fetchall()}
        for column, definition in FILM_SCENE_MIGRATIONS.items():
            if column not in scene_existing:
                conn.execute(f"ALTER TABLE film_scenes ADD COLUMN {column} {definition}")
        render_existing = {row["name"] for row in conn.execute("PRAGMA table_info(film_render_jobs)").fetchall()}
        for column, definition in FILM_RENDER_JOB_MIGRATIONS.items():
            if column not in render_existing:
                conn.execute(f"ALTER TABLE film_render_jobs ADD COLUMN {column} {definition}")
        resource_existing = {row["name"] for row in conn.execute("PRAGMA table_info(film_provider_resources)").fetchall()}
        for column, definition in FILM_RESOURCE_MIGRATIONS.items():
            if column not in resource_existing:
                conn.execute(f"ALTER TABLE film_provider_resources ADD COLUMN {column} {definition}")
        _migrate_film_scene_composite_key(conn)
