import httpx

async def chat_gemini(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    transcript = "\n\n".join(
        f"{'Người dùng' if m['role'] == 'user' else 'Trợ lý'}: {m['content']}"
        for m in messages if m["role"] in {"user", "assistant"}
    )
    payload = {"model": model, "input": transcript}
    if system:
        payload["system_instruction"] = system
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{base_url.rstrip('/')}/v1beta/interactions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    parts = []
    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            parts.extend(x.get("text", "") for x in step.get("content", []) if x.get("type") == "text")
    return "\n".join(parts).strip() or data.get("output_text", "") or "Không nhận được nội dung trả lời từ Gemini."
