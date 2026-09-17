import httpx

async def chat_openai(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    payload = {
        "model": model,
        "input": [{"role": m["role"], "content": m["content"]} for m in messages],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{base_url.rstrip('/')}/responses", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(content["text"])
    if not parts and data.get("output_text"):
        parts.append(data["output_text"])
    return "\n".join(parts).strip() or "Không nhận được nội dung trả lời từ OpenAI."
