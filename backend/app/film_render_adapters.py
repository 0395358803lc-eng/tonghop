import os
import re
import httpx

from .flow_bridge_client import render_flow_video
from .flow_store import get_flow_settings


class RenderAdapterError(RuntimeError):
    pass


class BaseRenderAdapter:
    id = 'none'
    name = 'Chưa cấu hình'

    @property
    def configured(self) -> bool:
        return False

    async def render(self, payload: dict) -> dict:
        raise RenderAdapterError('Render adapter chưa được cấu hình.')


class HttpJsonRenderAdapter(BaseRenderAdapter):
    id = 'http_json'
    name = 'Custom HTTP Video API'

    def __init__(self):
        self.url = os.getenv('FILM_RENDER_API_URL', '').strip()
        self.api_key = os.getenv('FILM_RENDER_API_KEY', '').strip()
        self.model = os.getenv('FILM_RENDER_MODEL', '').strip()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    async def render(self, payload: dict) -> dict:
        if not self.configured:
            raise RenderAdapterError('Thiếu FILM_RENDER_API_URL.')
        body = dict(payload)
        if self.model:
            body['model'] = self.model
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        timeout = httpx.Timeout(float(os.getenv('FILM_RENDER_TIMEOUT', '600')))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        result_url = data.get('result_url') or data.get('video_url') or data.get('url')
        if not result_url:
            raise RenderAdapterError('Render API không trả result_url/video_url/url.')
        return {
            'provider_job_id': data.get('id') or data.get('job_id'),
            'result_url': result_url,
            'first_frame_url': data.get('first_frame_url'),
            'last_frame_url': data.get('last_frame_url'),
            'raw': data,
        }


class FlowBridgeRenderAdapter(BaseRenderAdapter):
    id = 'flow_bridge'
    name = 'Google Flow · Local Session Bridge'

    @property
    def configured(self) -> bool:
        cfg = get_flow_settings()
        return bool(cfg and cfg.get('enabled') and cfg.get('bridge_url') and cfg.get('api_key'))

    async def render(self, payload: dict) -> dict:
        if not self.configured:
            raise RenderAdapterError('Flow Bridge chưa được cấu hình hoặc chưa bật.')
        try:
            return await render_flow_video(payload)
        except Exception as exc:
            message = str(exc)
            if re.match(r'^[A-Z][A-Z0-9_]+:\s*', message):
                raise RenderAdapterError(message) from exc
            raise RenderAdapterError(f'FLOW_RUNTIME_ERROR: Flow Bridge render thất bại: {message}') from exc


def get_active_adapter() -> BaseRenderAdapter:
    selected = os.getenv('FILM_RENDER_ADAPTER', '').strip().lower()
    flow = FlowBridgeRenderAdapter()
    if selected in {'flow', 'flow_bridge'} or (not selected and flow.configured):
        return flow
    if selected in {'http', 'http_json', 'custom_http'} or os.getenv('FILM_RENDER_API_URL', '').strip():
        return HttpJsonRenderAdapter()
    return BaseRenderAdapter()


def get_adapter_status() -> dict:
    adapter = get_active_adapter()
    return {
        'id': adapter.id,
        'name': adapter.name,
        'configured': adapter.configured,
        'supports_reference_frame': adapter.id in {'http_json', 'flow_bridge'},
        'contract': ('Flow Bridge submit/poll/result' if adapter.id == 'flow_bridge' else 'POST JSON -> result_url/video_url/url' if adapter.id == 'http_json' else None),
    }
