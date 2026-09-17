import asyncio
from .film_render_adapters import get_active_adapter, get_adapter_status
from .film_qc_service import get_qc_status, run_render_qc
from .film_render_store import (
    create_render_jobs, get_render_job, get_render_queue, list_render_jobs,
    next_waiting_job, refresh_render_job_context, retry_render_job, set_render_queue_paused, update_render_job,
)
from .film_service import audit_film_continuity
from .film_store import get_film_project

_project_locks: dict[str, asyncio.Lock] = {}


def _lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]


def render_status(project_id: str) -> dict:
    return {
        'adapter': get_adapter_status(),
        'qc_adapter': get_qc_status(),
        'queue': get_render_queue(project_id),
        'jobs': list_render_jobs(project_id, latest_only=True),
    }


def _assert_renderable(project: dict, scene_ids: list[str]) -> list[str]:
    scene_map = {scene['id']: scene for scene in project.get('scenes') or []}
    valid = [scene_id for scene_id in scene_ids if scene_id in scene_map]
    if not valid:
        raise ValueError('Không có scene hợp lệ để render.')
    blocked = []
    for scene_id in valid:
        warnings = [w for w in (scene_map[scene_id].get('warnings') or []) if not str(w).startswith('AUTO CONTINUITY:')]
        if warnings:
            blocked.append(f"{scene_id}: {warnings[0]}")
        if not (scene_map[scene_id].get('flow_prompt') or '').strip():
            blocked.append(f'{scene_id}: chưa có Flow Prompt.')
    if blocked:
        raise ValueError('CONTINUITY WARNING — chưa cho phép render: ' + ' | '.join(blocked[:8]))
    return valid


def enqueue_render(project_id: str, scene_ids: list[str] | None = None):
    project = audit_film_continuity(project_id) or get_film_project(project_id)
    if not project:
        raise ValueError('Không tìm thấy dự án phim.')
    if project.get('status') != 'ready':
        raise ValueError('Dự án phải hoàn tất Story Analysis trước khi render.')
    requested = scene_ids or [scene['id'] for scene in project.get('scenes') or []]
    valid = _assert_renderable(project, requested)
    adapter = get_active_adapter()
    blocked_reason = None if adapter.configured else 'Render adapter chưa cấu hình; job đang chờ adapter thật.'
    return create_render_jobs(project_id, valid, adapter.id, blocked_reason)


def retry_render(job_id: str):
    adapter = get_active_adapter()
    blocked_reason = None if adapter.configured else 'Render adapter chưa cấu hình; job retry đang chờ adapter thật.'
    return retry_render_job(job_id, blocked_reason)


async def process_render_queue(project_id: str):
    async with _lock(project_id):
        adapter = get_active_adapter()
        if not adapter.configured:
            return
        while True:
            queue = get_render_queue(project_id)
            if queue.get('paused'):
                return
            job = next_waiting_job(project_id)
            if not job:
                return
            current = get_render_job(job['id'])
            if not current or current['status'] != 'waiting':
                continue
            project = audit_film_continuity(project_id) or get_film_project(project_id)
            scene = next((x for x in (project.get('scenes') or []) if x['id'] == job['scene_id']), None) if project else None
            if not scene:
                update_render_job(job['id'], status='failed', progress=100, error='Scene không còn tồn tại.')
                continue
            blocking = [w for w in (scene.get('warnings') or []) if not str(w).startswith('AUTO CONTINUITY:')]
            if blocking:
                update_render_job(job['id'], status='failed', progress=100, error='Continuity thay đổi sau khi queue: ' + blocking[0])
                continue
            current = refresh_render_job_context(job['id']) or current
            try:
                update_render_job(job['id'], status='preparing', progress=10, error=None)
                payload = {
                    'project_id': project_id,
                    'scene_id': scene['id'],
                    'prompt': current['prompt'],
                    'duration': scene['duration'],
                    'aspect_ratio': project.get('settings', {}).get('aspect_ratio', '16:9'),
                    'resolution': project.get('settings', {}).get('resolution', '1080p'),
                    'reference_image_url': current['reference'].get('previous_last_frame_url'),
                    'previous_video_url': current['reference'].get('previous_result_url'),
                    'previous_end_state': current['reference'].get('previous_end_state'),
                    'start_state': scene.get('start_state'),
                    'end_state': scene.get('end_state'),
                }
                update_render_job(job['id'], status='generating', progress=35)
                result = await adapter.render(payload)
                update_render_job(job['id'], status='generating', progress=90, provider_job_id=result.get('provider_job_id'))
                completed = update_render_job(
                    job['id'], status='completed', progress=100, result_url=result['result_url'],
                    first_frame_url=result.get('first_frame_url'), last_frame_url=result.get('last_frame_url'),
                    qc_status='pending', qc_json={'message': 'Video đã render; đang chuyển sang Quality Control.'}, error=None,
                )
                qc = await run_render_qc(completed, project, scene)
                update_render_job(
                    job['id'], qc_status=qc['qc_status'], consistency_score=qc.get('consistency_score'), qc_json=qc.get('qc') or {},
                )
            except Exception as exc:
                update_render_job(job['id'], status='failed', progress=100, error=str(exc)[:2000])


def pause_render_queue(project_id: str):
    return set_render_queue_paused(project_id, True)


def resume_render_queue(project_id: str):
    return set_render_queue_paused(project_id, False)
