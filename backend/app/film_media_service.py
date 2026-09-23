import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from runtime_dependencies import ffmpeg_path

from .config import MEDIA_DIR
from .db import connect
from .film_media_store import (
    bind_render_job_media,
    bind_resource_media,
    get_media,
    get_media_by_provider_job,
    list_project_media,
    media_roots,
    project_media_root,
    public_media,
    register_completed_media,
    select_media,
    validate_media_path,
)
from .film_render_store import get_render_job, list_render_jobs
from .film_resource_store import list_project_resources, update_resource_binding
from .film_store import get_film_project

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
VIDEO_EXT = {'.mp4', '.mov', '.webm', '.m4v'}


def _probe_image(path: Path) -> dict:
    with Image.open(path) as image:
        width, height = image.size
        mime = {
            'JPEG': 'image/jpeg',
            'PNG': 'image/png',
            'WEBP': 'image/webp',
            'GIF': 'image/gif',
        }.get(image.format or '', 'image/jpeg')
    return {'width': width, 'height': height, 'mime_type': mime, 'duration_seconds': None}


def _probe_video(path: Path) -> dict:
    width = height = None
    duration = None
    try:
        import av
        container = av.open(str(path))
        try:
            stream = next((s for s in container.streams if s.type == 'video'), None)
            if stream and stream.width and stream.height:
                width, height = int(stream.width), int(stream.height)
            if container.duration:
                duration = round(float(container.duration) / 1_000_000, 3)
        finally:
            container.close()
    except Exception:
        pass
    mime = {
        '.mp4': 'video/mp4',
        '.m4v': 'video/mp4',
        '.webm': 'video/webm',
        '.mov': 'video/quicktime',
    }.get(path.suffix.lower(), 'video/mp4')
    return {'width': width, 'height': height, 'mime_type': mime, 'duration_seconds': duration}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_copy_thumbnail(source: Path, dest: Path) -> Path | None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        return dest
    except Exception:
        return None


def make_thumbnail(file_path: Path, media_type: str, preferred: Path | None = None, key: str | None = None, project_id: str | None = None) -> Path | None:
    folder = (project_media_root(project_id) / 'thumbnails') if project_id else (MEDIA_DIR / 'generated_media' / 'thumbnails')
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(Path(file_path).resolve()).encode('utf-8', 'replace')).hexdigest()[:16]
    safe_key = re.sub(r'[^A-Za-z0-9._-]+', '_', str(key or ''))[:48].strip('._')
    dest = folder / f"{(safe_key + '_') if safe_key else ''}{digest}_thumb.jpg"
    if preferred and preferred.exists() and preferred.is_file():
        copied = _safe_copy_thumbnail(preferred, dest)
        if copied:
            return copied
    if media_type == 'image':
        try:
            image = Image.open(file_path).convert('RGB')
            image.thumbnail((640, 360))
            image.save(dest, 'JPEG', quality=86, optimize=True)
            return dest
        except Exception:
            return None
    try:
        subprocess.run(
            [ffmpeg_path(), '-y', '-ss', '0.2', '-i', str(file_path), '-frames:v', '1', '-vf', 'scale=640:-1', str(dest)],
            check=True, capture_output=True, timeout=20,
        )
        if dest.exists() and dest.stat().st_size > 0:
            return dest
    except Exception:
        return None
    return None


def _flow_job_folder(result_url: str | None) -> Path | None:
    match = re.search(r'/api/flow/render/([0-9a-fA-F-]{36})/', result_url or '')
    if not match:
        return None
    job_id = match.group(1)
    for root in media_roots():
        folder = root / 'flow_downloads' / job_id
        if folder.exists():
            return folder
        matches = list(root.glob(f'projects/*/flow_downloads/{job_id}'))
        if matches:
            return matches[0]
    return None


def resolve_video_file(job: dict) -> Path | None:
    folder = _flow_job_folder(job.get('result_url'))
    if folder:
        for path in sorted(folder.glob('result.*')):
            if path.suffix.lower() in VIDEO_EXT and path.is_file():
                return path
    raw = job.get('result_url')
    if raw:
        candidate = Path(str(raw))
        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXT:
            try:
                return validate_media_path(candidate)
            except ValueError:
                return None
    return None


def resolve_first_frame(job: dict) -> Path | None:
    folder = _flow_job_folder(job.get('first_frame_url') or job.get('result_url'))
    if not folder:
        return None
    frames = sorted(folder.glob('first_frame_*.jpg'))
    return frames[0] if frames else None


def _qc_passed(qc_status: str | None, qc: dict | None) -> bool:
    if qc_status == 'passed':
        return True
    gate = (qc or {}).get('hard_gate') if isinstance(qc, dict) else None
    return bool(isinstance(gate, dict) and gate.get('passed') is True)


def apply_selection_to_pipeline(media: dict) -> dict:
    if not media:
        raise ValueError('MEDIA_NOT_FOUND')
    project_id = media['project_id']
    if media.get('resource_type') and media.get('entity_id'):
        provider = 'flow'
        bind_resource_media(project_id, media['resource_type'], media['entity_id'], media['id'], provider)
        qc = media.get('qc') if isinstance(media.get('qc'), dict) else {}
        passed = media.get('qc_status') == 'passed'
        patch = {
            'selected_media_id': media['id'],
            'selected_version': media.get('version'),
            'canonical_qc_status': media.get('qc_status'),
            'visual_lock': 'LOCKED' if passed else 'QC_REQUIRED',
        }
        if qc:
            patch['canonical_qc'] = qc
        sha = (media.get('metadata') or {}).get('canonical_sha256')
        if sha:
            patch['canonical_sha256'] = sha
        update_resource_binding(
            project_id,
            media['resource_type'],
            media['entity_id'],
            provider=provider,
            local_path=str(media.get('file_path') or ''),
            status='locked' if passed else None,
            error=None,
            metadata_patch=patch,
        )
    if media.get('scene_id') and media.get('media_type') == 'video':
        file_url = f"/api/film/media/{media['id']}/file"
        with connect() as conn:
            conn.execute(
                'UPDATE film_scenes SET result_url=?, updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND id=?',
                (file_url, project_id, media['scene_id']),
            )
        job_id = (media.get('metadata') or {}).get('render_job_id')
        if job_id:
            bind_render_job_media(str(job_id), media['id'])
    return media


def select_production_media(media_id: str) -> dict:
    selected = select_media(media_id)
    apply_selection_to_pipeline(selected)
    return get_media(media_id) or selected


def register_canonical_from_resource(resource: dict) -> dict | None:
    local_path = str(resource.get('local_path') or '')
    if not local_path:
        return None
    try:
        path = validate_media_path(local_path)
    except ValueError:
        return None
    metadata = dict(resource.get('metadata') or {})
    qc = metadata.get('canonical_qc') if isinstance(metadata.get('canonical_qc'), dict) else {}
    gate = qc.get('hard_gate') if isinstance(qc.get('hard_gate'), dict) else {}
    passed = bool(gate.get('passed') is True)
    qc_status = 'passed' if passed else ('failed' if qc else str(metadata.get('canonical_qc_status') or 'pending'))
    if qc_status not in {'passed', 'failed', 'pending', 'not_run', 'error'}:
        qc_status = 'pending'
    role = 'canonical_image' if passed else 'repair_candidate'
    probe = _probe_image(path)
    digest = metadata.get('canonical_sha256') or _sha256(path)
    provider_job_id = f"canonical:{resource['project_id']}:{resource['resource_type']}:{resource['entity_id']}:{digest[:16]}"
    thumb = make_thumbnail(path, 'image', key=f"{resource.get('resource_type')}_{resource.get('entity_id')}_{digest[:12]}", project_id=resource['project_id'])
    record = register_completed_media(
        project_id=resource['project_id'],
        media_type='image',
        role=role,
        file_path=path,
        resource_type=resource.get('resource_type'),
        entity_id=resource.get('entity_id'),
        provider=str(metadata.get('generation_provider') or resource.get('provider') or 'flow'),
        model=str(metadata.get('generation_model') or '') or None,
        provider_job_id=provider_job_id,
        thumbnail_path=thumb,
        mime_type=probe['mime_type'],
        file_size=path.stat().st_size,
        width=probe['width'],
        height=probe['height'],
        qc_status='passed' if passed else qc_status,
        qc_score=qc.get('overall_score') if isinstance(qc.get('overall_score'), (int, float)) else (gate.get('overall') or {}).get('score'),
        qc=qc,
        metadata={
            'source': 'canonical',
            'asset_version': metadata.get('asset_version'),
            'download_name': path.name,
            'resource_status': resource.get('status'),
            'visual_lock': metadata.get('visual_lock'),
            'canonical_sha256': digest,
        },
        select_if_passed=passed,
    )
    if record and record.get('is_selected'):
        apply_selection_to_pipeline(record)
    return record


def register_scene_video_from_job(job: dict, qc_result: dict | None = None) -> dict | None:
    path = resolve_video_file(job)
    if not path:
        return None
    qc_payload = qc_result or {}
    qc = qc_payload.get('qc') if isinstance(qc_payload.get('qc'), dict) else (job.get('qc') if isinstance(job.get('qc'), dict) else {})
    qc_status = str(qc_payload.get('qc_status') or job.get('qc_status') or 'pending')
    passed = _qc_passed(qc_status, qc)
    role = 'scene_video' if passed else 'repair_candidate'
    probe = _probe_video(path)
    first_frame = resolve_first_frame(job)
    thumb = make_thumbnail(path, 'video', first_frame, key=str(job.get('id') or job.get('scene_id') or ''), project_id=job['project_id'])
    provider_job_id = str(job.get('provider_job_id') or job.get('id'))
    score = qc_payload.get('consistency_score')
    if score is None:
        score = job.get('consistency_score')
    record = register_completed_media(
        project_id=job['project_id'],
        media_type='video',
        role=role,
        file_path=path,
        scene_id=job.get('scene_id'),
        provider=job.get('adapter') or 'flow_bridge',
        model=None,
        provider_job_id=f"render:{provider_job_id}",
        thumbnail_path=thumb or first_frame,
        mime_type=probe['mime_type'],
        file_size=path.stat().st_size,
        width=probe['width'],
        height=probe['height'],
        duration_seconds=probe['duration_seconds'],
        qc_status='passed' if passed else qc_status,
        qc_score=score,
        qc=qc,
        metadata={
            'source': 'scene_render',
            'render_job_id': job.get('id'),
            'scene_index': job.get('scene_index'),
            'download_name': path.name,
            'first_frame_url': job.get('first_frame_url'),
            'last_frame_url': job.get('last_frame_url'),
            'provider_error_code': job.get('provider_error_code'),
        },
        select_if_passed=passed,
    )
    if record:
        bind_render_job_media(job['id'], record['id'])
        if record.get('is_selected'):
            apply_selection_to_pipeline(record)
    return record


def backfill_project_media(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError('Không tìm thấy dự án phim')
    created = []
    for resource in list_project_resources(project_id, 'flow'):
        if resource.get('status') == 'retired' or not resource.get('local_path'):
            continue
        try:
            record = register_canonical_from_resource(resource)
            if record:
                created.append(record['id'])
        except Exception:
            continue
    for job in list_render_jobs(project_id, latest_only=False):
        if not job.get('result_url'):
            continue
        try:
            record = register_scene_video_from_job(job)
            if record:
                created.append(record['id'])
        except Exception:
            continue
    return {'project_id': project_id, 'upserted': len(set(created)), 'media': [public_media(item) for item in list_project_media(project_id)]}


def delete_project_media_files(project_id: str) -> dict:
    project_root = project_media_root(project_id)
    removed_files = 0
    removed_dirs = 0
    candidates: set[Path] = set()

    for media in list_project_media(project_id):
        for key in ('file_path', 'thumbnail_path'):
            raw = str(media.get(key) or '').strip()
            if raw:
                candidates.add(Path(raw))
    for resource in list_project_resources(project_id):
        raw = str(resource.get('local_path') or '').strip()
        if raw:
            candidates.add(Path(raw))

    if project_root.exists():
        shutil.rmtree(project_root, ignore_errors=True)
        removed_dirs += 1

    roots = media_roots()
    legacy_project_dirs: set[Path] = set()
    for raw in candidates:
        try:
            path = validate_media_path(raw)
        except ValueError:
            continue
        if path.is_relative_to(project_root):
            continue
        for root in roots:
            try:
                rel = path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) >= 2 and parts[0] in {'film_assets', 'final_films'} and parts[1] == project_id:
                legacy_project_dirs.add(root / parts[0] / project_id)
            elif len(parts) >= 2 and parts[0] in {'flow_downloads', 'flow_image_downloads'}:
                legacy_project_dirs.add(root / parts[0] / parts[1])
            break
        if path.exists() and path.is_file():
            try:
                path.unlink()
                removed_files += 1
            except OSError:
                pass

    for folder in sorted(legacy_project_dirs, key=lambda item: len(item.parts), reverse=True):
        if folder.exists() and folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed_dirs += 1

    return {'project_id': project_id, 'removed_files': removed_files, 'removed_dirs': removed_dirs}


def list_public_media(project_id: str, **filters) -> list[dict]:
    existing = list_project_media(project_id)
    if not existing:
        backfill_project_media(project_id)
    return [public_media(item) for item in list_project_media(project_id, **filters)]
