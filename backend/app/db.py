import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

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
 visual_style TEXT, timeline_json TEXT DEFAULT '[]', error TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS film_scenes (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scene_index INTEGER NOT NULL, title TEXT, source_text TEXT, summary TEXT,
 duration REAL NOT NULL DEFAULT 8, characters_json TEXT DEFAULT '[]', location_id TEXT, action TEXT, camera TEXT,
 lighting TEXT, atmosphere TEXT, voiceover TEXT, dialogue_json TEXT DEFAULT '[]', start_state TEXT, end_state TEXT,
 continuity_json TEXT DEFAULT '{}', visual_prompt TEXT, flow_prompt TEXT, warnings_json TEXT DEFAULT '[]',
 render_status TEXT NOT NULL DEFAULT 'not_configured', result_url TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
 provider_job_id TEXT, result_url TEXT, first_frame_url TEXT, last_frame_url TEXT, error TEXT,
 qc_status TEXT NOT NULL DEFAULT 'not_run', consistency_score REAL, qc_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(project_id) REFERENCES film_projects(id) ON DELETE CASCADE,
 FOREIGN KEY(scene_id) REFERENCES film_scenes(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_film_render_jobs_project ON film_render_jobs(project_id, scene_index, created_at);
CREATE INDEX IF NOT EXISTS idx_film_render_jobs_scene ON film_render_jobs(project_id, scene_id, created_at);
"""

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

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
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(video_jobs)").fetchall()}
        for column, definition in VIDEO_JOB_MIGRATIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE video_jobs ADD COLUMN {column} {definition}")
