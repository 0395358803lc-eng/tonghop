import httpx

from .providers.registry import PROVIDERS


def _auth_error(provider: str, status: int, response: httpx.Response) -> ValueError:
    name = PROVIDERS.get(provider, {}).get("name", provider)
    if status == 401:
        return ValueError(f"API key {name} không hợp lệ, đã bị thu hồi hoặc bị vô hiệu hóa.")
    if status == 403:
        return ValueError(f"API key {name} hợp lệ nhưng không có quyền truy cập tài nguyên này.")
    try:
        payload = response.json()
        detail = payload.get("error", {}).get("message") or payload.get("message") or response.text
    except Exception:
        detail = response.text
    return ValueError(f"{name} trả HTTP {status}: {str(detail)[:400]}")


async def validate_provider_credentials(provider: str, api_key: str, base_url: str | None = None) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Nhà cung cấp không tồn tại")
    base = (base_url or PROVIDERS[provider]["base_url"]).rstrip("/")
    headers = {}
    if provider == "openai":
        url = f"{base}/models"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "anthropic":
        url = f"{base}/v1/models?limit=1"
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    elif provider == "gemini":
        url = f"{base}/v1beta/models?pageSize=1"
        headers["x-goog-api-key"] = api_key
    elif provider == "openrouter":
        url = f"{base}/auth/key"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "xkiro":
        # /models của xKiro là public nên không thể dùng để xác thực key.
        # /usage yêu cầu auth và không tạo inference/token cost.
        url = f"{base}/usage"
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        return {"ok": True, "verified": False}

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Không kết nối được tới {PROVIDERS[provider]['name']}: {exc}") from exc

    if response.status_code >= 400:
        raise _auth_error(provider, response.status_code, response)
    return {"ok": True, "verified": True, "provider": provider}
