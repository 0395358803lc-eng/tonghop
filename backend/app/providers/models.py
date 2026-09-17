import httpx
from .registry import DEFAULT_MODELS, PROVIDERS

async def list_models(provider: str, api_key: str | None, base_url: str | None = None) -> list[str]:
    cfg = PROVIDERS[provider]
    base = (base_url or cfg["base_url"]).rstrip("/")
    headers = {}
    url = ""
    if provider == "openai":
        headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base}/models"
    elif provider == "anthropic":
        headers.update({"x-api-key": api_key or "", "anthropic-version": "2023-06-01"})
        url = f"{base}/v1/models?limit=100"
    elif provider == "gemini":
        headers["x-goog-api-key"] = api_key or ""
        url = f"{base}/v1beta/models?pageSize=1000"
    elif provider in {"openrouter", "xkiro"}:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base}/models"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        if provider == "gemini":
            values = [m.get("name", "").removeprefix("models/") for m in data.get("models", [])]
        else:
            values = [m.get("id", "") for m in data.get("data", [])]
        values = [v for v in values if v and not any(x in v.lower() for x in ("embedding", "moderation", "audio", "tts", "image"))]
        return sorted(set(values), key=str.lower)
    except Exception:
        return DEFAULT_MODELS.get(provider, [])
