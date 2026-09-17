import os
import httpx


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


def get_active_adapter() -> BaseRenderAdapter:
    selected = os.getenv('FILM_RENDER_ADAPTER', '').strip().lower()
    if selected in {'http', 'http_json', 'custom_http'} or os.getenv('FILM_RENDER_API_URL', '').strip():
        return HttpJsonRenderAdapter()
    return BaseRenderAdapter()


def get_adapter_status() -> dict:
    adapter = get_active_adapter()
    return {
        'id': adapter.id,
        'name': adapter.name,
        'configured': adapter.configured,
        'supports_reference_frame': adapter.id == 'http_json',
        'contract': 'POST JSON -> result_url/video_url/url' if adapter.id == 'http_json' else None,
    }
