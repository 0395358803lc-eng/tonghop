import httpx
from urllib.parse import quote
from .registry import PROVIDERS


def _openai_text(data: dict) -> str:
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(content["text"])
    if not parts and data.get("output_text"):
        parts.append(data["output_text"])
    return "\n".join(parts).strip()


def _compatible_text(data: dict) -> str:
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict)).strip()
    return str(content or "").strip()


def _image_label(image: dict) -> str:
    label = str(image.get("label") or "").strip()
    if label:
        return label
    try:
        return f"Frame tại {float(image.get('timestamp') or 0):.1f} giây"
    except Exception:
        return "Hình ảnh tham chiếu"


async def run_vision(provider: str, api_key: str, base_url: str | None, model: str, prompt: str, images: list[dict]) -> str:
    cfg = PROVIDERS[provider]
    base = (base_url or cfg["base_url"]).rstrip("/")
    kind = cfg["kind"]
    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if kind == "openai":
            content = [{"type": "input_text", "text": prompt}]
            for image in images:
                content.append({"type": "input_text", "text": _image_label(image) + ":"})
                content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{image['data']}"})
            response = await client.post(f"{base}/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "input": [{"role": "user", "content": content}]})
            response.raise_for_status()
            return _openai_text(response.json())

        if kind == "anthropic":
            content = [{"type": "text", "text": prompt}]
            for image in images:
                content.append({"type": "text", "text": _image_label(image) + ":"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image["data"]}})
            response = await client.post(f"{base}/v1/messages", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": model, "max_tokens": 5000, "messages": [{"role": "user", "content": content}]})
            response.raise_for_status()
            data = response.json()
            return "\n".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text").strip()

        if kind == "gemini":
            parts = [{"text": prompt}]
            for image in images:
                parts.append({"text": _image_label(image) + ":"})
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image["data"]}})
            clean_model = model.removeprefix("models/")
            response = await client.post(f"{base}/v1beta/models/{quote(clean_model, safe='')}:generateContent", headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json={"contents": [{"role": "user", "parts": parts}]})
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            return "\n".join(x.get("text", "") for x in candidates[0].get("content", {}).get("parts", []) if x.get("text")).strip()

        content = [{"type": "text", "text": prompt}]
        for image in images:
            content.append({"type": "text", "text": _image_label(image) + ":"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image['data']}", "detail": "low"}})
        response = await client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "user", "content": content}], "stream": False})
        response.raise_for_status()
        return _compatible_text(response.json())
