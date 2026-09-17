import json
import uuid
from .db import connect


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _job(row):
    if not row:
        return None
    data = dict(row)
    data['reference'] = _loads(data.pop('reference_json', None), {})
    data['qc'] = _loads(data.pop('qc_json', None), {})
    data['progress'] = int(data.get('progress') or 0)
    data['attempt'] = int(data.get('attempt') or 0)
    return data


def ensure_render_queue(project_id: str, adapter: str | None = None):
    with connect() as conn:
        existing = conn.execute('SELECT adapter FROM film_render_queues WHERE project_id=?', (project_id,)).fetchone()
        value = adapter if adapter is not None else (existing['adapter'] if existing else 'none')
        conn.execute("""INSERT INTO film_render_queues(project_id,adapter,paused) VALUES(?,?,0)
        ON CONFLICT(project_id) DO UPDATE SET adapter=excluded.adapter,updated_at=CURRENT_TIMESTAMP""", (project_id, value))
    return get_render_queue(project_id)


def get_render_queue(project_id: str):
    with connect() as conn:
        row = conn.execute('SELECT * FROM film_render_queues WHERE project_id=?', (project_id,)).fetchone()
    if not row:
        return {'project_id': project_id, 'adapter': 'none', 'paused': False}
    data = dict(row)
    data['paused'] = bool(data['paused'])
    return data


def set_render_queue_paused(project_id: str, paused: bool):
    ensure_render_queue(project_id)
    with connect() as conn:
        conn.execute('UPDATE film_render_queues SET paused=?,updated_at=CURRENT_TIMESTAMP WHERE project_id=?', (1 if paused else 0, project_id))
    return get_render_queue(project_id)


def list_render_jobs(project_id: str, latest_only: bool = False):
    with connect() as conn:
        if latest_only:
            rows = conn.execute("""SELECT j.* FROM film_render_jobs j JOIN (
              SELECT scene_id,MAX(rowid) max_rowid FROM film_render_jobs WHERE project_id=? GROUP BY scene_id
            ) x ON j.rowid=x.max_rowid WHERE j.project_id=? ORDER BY j.scene_index""", (project_id, project_id)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM film_render_jobs WHERE project_id=? ORDER BY created_at DESC,scene_index', (project_id,)).fetchall()
    return [_job(row) for row in rows]


def get_render_job(job_id: str):
    with connect() as conn:
        row = conn.execute('SELECT * FROM film_render_jobs WHERE id=?', (job_id,)).fetchone()
    return _job(row)


def _scene_context(conn, project_id: str, scene_id: str):
    scene = conn.execute('SELECT * FROM film_scenes WHERE project_id=? AND id=?', (project_id, scene_id)).fetchone()
    if not scene:
        return None
    prev = conn.execute('SELECT * FROM film_scenes WHERE project_id=? AND scene_index=?', (project_id, scene['scene_index'] - 1)).fetchone()
    nxt = conn.execute('SELECT * FROM film_scenes WHERE project_id=? AND scene_index=?', (project_id, scene['scene_index'] + 1)).fetchone()
    previous_render = None
    if prev:
        previous_render = conn.execute("""SELECT result_url,last_frame_url FROM film_render_jobs
        WHERE project_id=? AND scene_id=? AND status='completed' ORDER BY created_at DESC LIMIT 1""", (project_id, prev['id'])).fetchone()
    reference = {
        'previous_scene_id': prev['id'] if prev else None,
        'previous_end_state': prev['end_state'] if prev else None,
        'previous_result_url': previous_render['result_url'] if previous_render else None,
        'previous_last_frame_url': previous_render['last_frame_url'] if previous_render else None,
        'current_start_state': scene['start_state'],
        'current_end_state': scene['end_state'],
        'next_scene_id': nxt['id'] if nxt else None,
        'next_start_state': nxt['start_state'] if nxt else None,
    }
    return scene, reference


def refresh_render_job_context(job_id: str):
    source = get_render_job(job_id)
    if not source:
        return None
    with connect() as conn:
        ctx = _scene_context(conn, source['project_id'], source['scene_id'])
        if not ctx:
            return None
        scene, reference = ctx
        prompt = scene['flow_prompt'] or scene['visual_prompt'] or ''
        conn.execute(
            'UPDATE film_render_jobs SET prompt=?,reference_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (prompt, json.dumps(reference, ensure_ascii=False), job_id),
        )
    return get_render_job(job_id)


def create_render_jobs(project_id: str, scene_ids: list[str], adapter: str, blocked_reason: str | None = None):
    jobs = []
    ensure_render_queue(project_id, adapter)
    with connect() as conn:
        for scene_id in scene_ids:
            ctx = _scene_context(conn, project_id, scene_id)
            if not ctx:
                continue
            scene, reference = ctx
            job_id = str(uuid.uuid4())
            prompt = scene['flow_prompt'] or scene['visual_prompt'] or ''
            status = 'waiting'
            error = blocked_reason
            conn.execute("""INSERT INTO film_render_jobs(
              id,project_id,scene_id,scene_index,adapter,status,progress,attempt,prompt,reference_json,error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                job_id, project_id, scene_id, scene['scene_index'], adapter, status, 0, 0,
                prompt, json.dumps(reference, ensure_ascii=False), error,
            ))
            conn.execute("UPDATE film_scenes SET render_status='waiting',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?", (project_id, scene_id))
            jobs.append(job_id)
    return [get_render_job(job_id) for job_id in jobs]


def retry_render_job(job_id: str, blocked_reason: str | None = None):
    source = get_render_job(job_id)
    if not source:
        return None
    with connect() as conn:
        ctx = _scene_context(conn, source['project_id'], source['scene_id'])
        if not ctx:
            return None
        scene, reference = ctx
        new_id = str(uuid.uuid4())
        conn.execute("""INSERT INTO film_render_jobs(
          id,project_id,scene_id,scene_index,adapter,status,progress,attempt,prompt,reference_json,error
        ) VALUES(?,?,?,?,?,'waiting',0,?,?,?,?)""", (
            new_id, source['project_id'], source['scene_id'], source['scene_index'], source['adapter'],
            source['attempt'] + 1, scene['flow_prompt'] or scene['visual_prompt'] or '',
            json.dumps(reference, ensure_ascii=False), blocked_reason,
        ))
        conn.execute("UPDATE film_scenes SET render_status='waiting',updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?", (source['project_id'], source['scene_id']))
    return get_render_job(new_id)


def update_render_job(job_id: str, **values):
    allowed = {'status','progress','provider_job_id','result_url','first_frame_url','last_frame_url','error','qc_status','consistency_score','qc_json'}
    patch = {k: v for k, v in values.items() if k in allowed}
    if isinstance(patch.get('qc_json'), (dict, list)):
        patch['qc_json'] = json.dumps(patch['qc_json'], ensure_ascii=False)
    if patch:
        parts = [f'{key}=?' for key in patch]
        with connect() as conn:
            conn.execute(f"UPDATE film_render_jobs SET {', '.join(parts)},updated_at=CURRENT_TIMESTAMP WHERE id=?", list(patch.values()) + [job_id])
            row = conn.execute('SELECT project_id,scene_id,status,result_url FROM film_render_jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                conn.execute('UPDATE film_scenes SET render_status=?,result_url=COALESCE(?,result_url),updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?',
                             (row['status'], row['result_url'], row['project_id'], row['scene_id']))
    return get_render_job(job_id)


def next_waiting_job(project_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM film_render_jobs WHERE project_id=? AND status='waiting' ORDER BY scene_index,created_at LIMIT 1", (project_id,)).fetchone()
    return _job(row)
