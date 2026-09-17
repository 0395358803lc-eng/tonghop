import httpx

async def chat_compatible(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    return str(content).strip() or "Không nhận được nội dung trả lời từ model."
