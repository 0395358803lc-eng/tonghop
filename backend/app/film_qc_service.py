import os
import httpx

QC_URL = os.getenv('FILM_QC_API_URL', '').strip()
QC_KEY = os.getenv('FILM_QC_API_KEY', '').strip()
QC_MIN_SCORE = float(os.getenv('FILM_QC_MIN_SCORE', '80'))


def get_qc_status() -> dict:
    return {
        'id': 'http_json_qc' if QC_URL else 'none',
        'name': 'Custom HTTP Vision QC' if QC_URL else 'Chưa cấu hình QC',
        'configured': bool(QC_URL),
        'min_score': QC_MIN_SCORE,
        'contract': 'POST JSON -> consistency_score/issues/passed' if QC_URL else None,
    }


async def run_render_qc(job: dict, project: dict, scene: dict) -> dict:
    if not QC_URL:
        return {'qc_status': 'not_configured', 'consistency_score': None, 'qc': {'message': 'Chưa cấu hình FILM_QC_API_URL.'}}
    payload = {
        'project_id': project['id'], 'scene_id': scene['id'], 'video_url': job.get('result_url'),
        'flow_prompt': scene.get('flow_prompt'), 'characters': project.get('characters') or [],
        'locations': project.get('locations') or [], 'visual_style': project.get('visual_style'),
        'start_state': scene.get('start_state'), 'end_state': scene.get('end_state'),
        'reference': job.get('reference') or {},
    }
    headers = {'Content-Type': 'application/json'}
    if QC_KEY:
        headers['Authorization'] = f'Bearer {QC_KEY}'
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv('FILM_QC_TIMEOUT', '180'))) as client:
            response = await client.post(QC_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        score = data.get('consistency_score', data.get('score'))
        score = float(score) if score is not None else None
        passed = data.get('passed') if isinstance(data.get('passed'), bool) else (score is not None and score >= QC_MIN_SCORE)
        return {'qc_status': 'passed' if passed else 'failed', 'consistency_score': score, 'qc': data}
    except Exception as exc:
        return {'qc_status': 'error', 'consistency_score': None, 'qc': {'error': str(exc)[:1600]}}
