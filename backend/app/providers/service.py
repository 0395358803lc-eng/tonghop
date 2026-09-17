from .anthropic_provider import chat_anthropic
from .gemini_provider import chat_gemini
from .openai_compatible import chat_compatible
from .openai_provider import chat_openai
from .registry import PROVIDERS

async def run_chat(provider: str, api_key: str, base_url: str | None, model: str, messages: list[dict]) -> str:
    cfg = PROVIDERS[provider]
    base = base_url or cfg["base_url"]
    kind = cfg["kind"]
    if kind == "openai":
        return await chat_openai(base, api_key, model, messages)
    if kind == "anthropic":
        return await chat_anthropic(base, api_key, model, messages)
    if kind == "gemini":
        return await chat_gemini(base, api_key, model, messages)
    return await chat_compatible(base, api_key, model, messages)
