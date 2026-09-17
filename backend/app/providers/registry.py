PROVIDERS = {
    "openai": {"name": "OpenAI", "kind": "openai", "accent": "#10a37f", "base_url": "https://api.openai.com/v1"},
    "gemini": {"name": "Google AI Studio", "kind": "gemini", "accent": "#4285f4", "base_url": "https://generativelanguage.googleapis.com"},
    "anthropic": {"name": "Claude / Anthropic", "kind": "anthropic", "accent": "#d97757", "base_url": "https://api.anthropic.com"},
    "openrouter": {"name": "OpenRouter", "kind": "openai_compatible", "accent": "#7c3aed", "base_url": "https://openrouter.ai/api/v1"},
    "xkiro": {"name": "xKiro", "kind": "openai_compatible", "accent": "#ec4899", "base_url": "https://api.xkiro.com/v1"},
}

DEFAULT_MODELS = {
    "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "gemini": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-2.5-pro"],
    "anthropic": ["claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6"],
    "openrouter": ["~openai/gpt-latest", "google/gemini-3.1-flash-lite"],
    "xkiro": ["openai/gpt-5.6-sol", "anthropic/claude-opus-5", "z-ai/glm-5.2"],
}
