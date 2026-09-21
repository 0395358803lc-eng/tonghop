from __future__ import annotations

import re


FLOW_DEPENDENCY_ERROR_CODES = frozenset({
    "SESSION_EXPIRED",
    "REAUTH_REQUIRED",
    "BRIDGE_AUTH_ERROR",
    "PROJECT_NOT_FOUND",
    "FLOW_PROJECT_NOT_FOUND",
    "FLOW_UI_CHANGED",
    "CAPABILITY_MISMATCH",
    "FLOW_CREDITS_INSUFFICIENT",
})


def render_error_code(message: str | None) -> str:
    text = str(message or "").strip()
    match = re.match(r"^([A-Z][A-Z0-9_]+):\s*", text)
    return match.group(1) if match else "RENDER_ERROR"


def is_flow_dependency_error(message: str | None) -> bool:
    return render_error_code(message) in FLOW_DEPENDENCY_ERROR_CODES
