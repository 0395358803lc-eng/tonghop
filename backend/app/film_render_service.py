import asyncio
import os
import re
from .film_render_adapters import get_active_adapter, get_adapter_status
from .film_qc_service import get_qc_status, run_render_qc
from .film_render_store import (
    create_render_jobs, get_render_job, get_render_queue, list_render_jobs,
    next_waiting_job, refresh_render_job_context, retry_render_job, set_render_queue_paused, update_render_job,
)
from .film_service import audit_film_continuity
from .film_integrity import verify_source_lock
from .film_production_gate import run_production_gate
from .film_store import get_film_project
from .film_resource_store import list_project_resources, lock_project_resources, scene_resource_manifest, sync_project_resources
from .film_media_service import register_scene_video_from_job

_project_locks: dict[str, asyncio.Lock] = {}
QC_AUTO_REGENERATE = os.getenv('FILM_QC_AUTO_REGENERATE', '1').strip().lower() not in {'0','false','no','off'}
QC_MAX_REGENERATIONS = max(0, min(int(os.getenv('FILM_QC_MAX_REGENERATIONS', '1')), 3))
QC_REQUIRE_PASS = os.getenv('FILM_QC_REQUIRE_PASS', '1').strip().lower() not in {'0','false','no','off'}


def _lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]


def render_status(project_id: str) -> dict:
    resources = list_project_resources(project_id, 'flow')
    resource_counts: dict[str, int] = {}
    for item in resources:
        status = str(item.get('status') or 'unknown')
        resource_counts[status] = resource_counts.get(status, 0) + 1
    active_resources = [x for x in resources if x.get('status') != 'retired']
    qc_passed = [
        x for x in active_resources
        if bool((((x.get('metadata') or {}).get('canonical_qc') or {}).get('hard_gate') or {}).get('passed') is True)
    ]
    return {
        'adapter': get_adapter_status(),
        'qc_adapter': get_qc_status(),
        'queue': get_render_queue(project_id),
        'jobs': list_render_jobs(project_id, latest_only=True),
        'resources': {
            'provider': 'flow',
            'total': len(active_resources),
            'by_status': resource_counts,
            'all_ready': bool(active_resources)
                and len(qc_passed) == len(active_resources)
                and all(x.get('status') in {'ready','locked'} for x in active_resources),
            'locked': sum(1 for x in active_resources if x.get('status') == 'locked'),
            'qc_passed': len(qc_passed),
            'qc_required': len(active_resources) - len(qc_passed),
        },
    }


def _assert_renderable(project: dict, scene_ids: list[str]) -> list[str]:
    consistency = project.get('consistency_report') or {}
    if not consistency or consistency.get('final_gate') is not True:
        effective = consistency.get('effective_errors') or consistency.get('review_items') or []
        detail = (
            effective[0].get('code') or effective[0].get('detail')
            if effective and isinstance(effective[0], dict)
            else 'CONSISTENCY_NOT_VERIFIED'
        )
        raise ValueError(
            f'CONSISTENCY_GATE_BLOCKED: Project chưa đạt kiểm tra tính nhất quán Rule + AI ({detail}). '
            'Hãy chạy Kiểm tra tính nhất quán hoặc Tự động sửa lỗi nhất quán trước khi render.'
        )
    production_gate = project.get('production_gate') or {}
    if not production_gate or production_gate.get('final_gate') is not True:
        errors = production_gate.get('errors') or []
        detail = errors[0].get('code') if errors and isinstance(errors[0], dict) else 'PRODUCTION_GATE_NOT_VERIFIED'
        raise ValueError(
            f'PRODUCTION_GATE_BLOCKED: Project chưa đạt Production Gate ({detail}). '
            'Hãy chạy Production Gate hoặc Auto Repair Derived trước khi render.'
        )
    scene_map = {scene['id']: scene for scene in project.get('scenes') or []}
    valid = [scene_id for scene_id in scene_ids if scene_id in scene_map]
    if not valid:
        raise ValueError('Không có scene hợp lệ để render.')
    blocked = []
    for scene_id in valid:
        scene = scene_map[scene_id]
        source_ok, source_error = verify_source_lock(scene)
        if not source_ok:
            blocked.append(f"{scene_id}: {source_error or 'SOURCE_LOCK_FAILED'}")
        shots = scene.get('shots') or []
        if len(shots) > 1:
            blocked.append(f"{scene_id}: SHOT_LEVEL_RENDER_REQUIRED ({len(shots)} shots)")
        warnings = [w for w in (scene.get('warnings') or []) if not str(w).startswith('AUTO CONTINUITY:')]
        if warnings:
            blocked.append(f"{scene_id}: {warnings[0]}")
        if not (scene.get('flow_prompt') or '').strip():
            blocked.append(f'{scene_id}: chưa có Flow Prompt.')
        if bool(project.get('settings', {}).get('require_provider_assets', False)):
            manifest = scene_resource_manifest(project, scene, 'flow')
            if not manifest.get('ready'):
                detail = ', '.join(manifest.get('missing') or []) or 'canonical assets chưa sẵn sàng'
                blocked.append(f"{scene_id}: RESOURCE_LOCK_INCOMPLETE ({detail})")
    if blocked:
        raise ValueError('CONTINUITY WARNING — chưa cho phép render: ' + ' | '.join(blocked[:8]))
    return valid


def enqueue_render(project_id: str, scene_ids: list[str] | None = None):
    project = get_film_project(project_id)
    if not project:
        raise ValueError('Không tìm thấy dự án phim.')
    consistency = project.get('consistency_report') or {}
    if consistency.get('final_gate') is not True:
        raise ValueError('CONSISTENCY_GATE_BLOCKED: Kiểm tra tính nhất quán V2 chưa PASS.')
    report = run_production_gate(project_id, persist=True)
    project = get_film_project(project_id) or project
    if report.get('final_gate') is not True or project.get('status') != 'ready':
        detail = (report.get('errors') or [{}])[0].get('code', 'PRODUCTION_GATE_FAILED')
        raise ValueError(f'PRODUCTION_GATE_BLOCKED: {detail}')
    sync_project_resources(project_id, 'flow')
    lock_project_resources(project_id, 'flow')
    project = get_film_project(project_id) or project
    requested = scene_ids or [scene['id'] for scene in project.get('scenes') or []]
    valid = _assert_renderable(project, requested)
    adapter = get_active_adapter()
    if adapter.id == 'flow_bridge' or bool(project.get('settings', {}).get('require_provider_assets', False)):
        scene_map = {scene['id']: scene for scene in project.get('scenes') or []}
        resource_errors = []
        for scene_id in valid:
            manifest = scene_resource_manifest(project, scene_map[scene_id], 'flow')
            if not manifest.get('ready'):
                missing = ', '.join(manifest.get('missing') or ['canonical reference'])
                resource_errors.append(f'{scene_id}: {missing}')
        if resource_errors:
            raise ValueError(
                'RESOURCE_LOCK_INCOMPLETE: Chưa thể tạo video vì thiếu tài nguyên hình ảnh chuẩn. '
                + ' | '.join(resource_errors[:8])
            )
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
            project = get_film_project(project_id)
            consistency = (project or {}).get('consistency_report') or {}
            if consistency.get('final_gate') is not True:
                update_render_job(
                    job['id'], status='failed', progress=100,
                    error='Kiểm tra tính nhất quán V2 không còn hợp lệ; dữ liệu có thể đã thay đổi.',
                    provider_error_code='CONSISTENCY_GATE_CHANGED',
                )
                return
            gate = run_production_gate(project_id, persist=True)
            project = get_film_project(project_id)
            if gate.get('final_gate') is not True:
                detail = (gate.get('errors') or [{}])[0].get('code', 'PRODUCTION_GATE_CHANGED')
                update_render_job(
                    job['id'], status='failed', progress=100,
                    error=f'Production Gate không còn hợp lệ: {detail}.',
                    provider_error_code='PRODUCTION_GATE_CHANGED',
                )
                return
            scene = next((x for x in (project.get('scenes') or []) if x['id'] == job['scene_id']), None) if project else None
            if not scene:
                update_render_job(job['id'], status='failed', progress=100, error='Scene không còn tồn tại.')
                continue
            blocking = [w for w in (scene.get('warnings') or []) if not str(w).startswith('AUTO CONTINUITY:')]
            if blocking:
                update_render_job(job['id'], status='failed', progress=100, error='Continuity thay đổi sau khi queue: ' + blocking[0])
                continue
            current = refresh_render_job_context(job['id']) or current
            reference = current.get('reference') or {}
            resource_manifest = scene_resource_manifest(project, scene, 'flow')
            require_assets = adapter.id == 'flow_bridge' or bool(project.get('settings', {}).get('require_provider_assets', False))
            if require_assets and not resource_manifest.get('ready'):
                missing = ', '.join(resource_manifest.get('missing') or [])
                update_render_job(
                    job['id'], status='failed', progress=100,
                    error='Scene chưa đủ canonical Character/Location/Prop assets đã khóa.'
                          + (f' Thiếu: {missing}' if missing else ''),
                    provider_error_code='RESOURCE_LOCK_INCOMPLETE',
                )
                return
            if reference.get('previous_scene_id') and not reference.get('previous_last_frame_url'):
                update_render_job(
                    job['id'], error='Đang chờ scene trước có last-frame đạt QC.',
                    provider_error_code='CONTINUITY_REFERENCE_PENDING',
                )
                return
            try:
                update_render_job(job['id'], status='preparing', progress=10, error=None)
                payload = {
                    'project_id': project_id,
                    'idempotency_key': job['id'],
                    'flow_project_id': project.get('settings', {}).get('flow_project_id'),
                    'model': project.get('settings', {}).get('flow_model'),
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
                    'resource_manifest': resource_manifest,
                }
                update_render_job(job['id'], status='generating', progress=35)
                result = await adapter.render(payload)
                update_render_job(job['id'], status='generating', progress=90, provider_job_id=result.get('provider_job_id'))
                completed = update_render_job(
                    job['id'], status='completed', progress=100, result_url=result['result_url'],
                    first_frame_url=result.get('first_frame_url'), last_frame_url=result.get('last_frame_url'),
                    qc_status='pending', qc_json={'message': 'Video đã render; đang chuyển sang Quality Control.'}, error=None, provider_error_code=None,
                )
                qc = await run_render_qc(completed, project, scene)
                update_render_job(
                    job['id'], qc_status=qc['qc_status'], consistency_score=qc.get('consistency_score'), qc_json=qc.get('qc') or {},
                )
                try:
                    register_scene_video_from_job(get_render_job(job['id']) or completed, qc)
                except Exception:
                    pass
                if qc['qc_status'] == 'failed':
                    score = qc.get('consistency_score')
                    message = f"Vision QC failed{f' ({score:.1f}/100)' if score is not None else ''}."
                    update_render_job(
                        job['id'], status='failed', progress=100, error=message, provider_error_code='QC_FAILED',
                    )
                    if QC_AUTO_REGENERATE and int(job.get('attempt') or 0) < QC_MAX_REGENERATIONS:
                        retry_render_job(job['id'])
                        continue
                    return
                if qc['qc_status'] == 'error' and QC_REQUIRE_PASS:
                    update_render_job(
                        job['id'], status='failed', progress=100,
                        error='Vision QC không hoàn tất; pipeline dừng để tránh tiếp tục bằng video chưa kiểm định.',
                        provider_error_code='QC_ERROR',
                    )
                    return
            except Exception as exc:
                message = str(exc)[:2000]
                match = re.match(r'^([A-Z][A-Z0-9_]+):\s*', message)
                provider_error_code = match.group(1) if match else 'RENDER_ERROR'
                update_render_job(
                    job['id'], status='failed', progress=100, error=message,
                    provider_error_code=provider_error_code,
                )


def pause_render_queue(project_id: str):
    return set_render_queue_paused(project_id, True)


def resume_render_queue(project_id: str):
    return set_render_queue_paused(project_id, False)
