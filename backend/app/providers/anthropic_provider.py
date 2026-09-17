import httpx

async def chat_anthropic(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    body = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in {"user", "assistant"}]
    payload = {"model": model, "max_tokens": 8192, "messages": body}
    if system:
        payload["system"] = system
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{base_url.rstrip('/')}/v1/messages", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    parts = [x.get("text", "") for x in data.get("content", []) if x.get("type") == "text"]
    return "\n".join(parts).strip() or "Không nhận được nội dung trả lời từ Claude."
