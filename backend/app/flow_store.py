import json
from urllib.parse import urlparse

from .crypto import decrypt, encrypt, mask
from .db import connect

SETTING_KEY = "flow_bridge"


def _validate_loopback_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Flow Bridge URL phải dùng http:// hoặc https://")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Flow Bridge chỉ được phép dùng địa chỉ loopback/local.")
    if not parsed.port:
        raise ValueError("Flow Bridge URL phải có port.")
    return raw


def save_flow_settings(bridge_url: str, api_key: str, enabled: bool = True) -> dict:
    url = _validate_loopback_url(bridge_url)
    key = api_key.strip()
    if len(key) < 16:
        raise ValueError("Flow Bridge API key phải có ít nhất 16 ký tự.")
    payload = json.dumps({"bridge_url": url, "api_key": key, "enabled": bool(enabled)}, ensure_ascii=False)
    with connect() as conn:
        conn.execute("""INSERT INTO secure_settings(key, encrypted_value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET encrypted_value=excluded.encrypted_value, updated_at=CURRENT_TIMESTAMP""",
        (SETTING_KEY, encrypt(payload)))
    return get_flow_status()


def get_flow_settings() -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT encrypted_value FROM secure_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(decrypt(row["encrypted_value"]))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def get_flow_status() -> dict:
    data = get_flow_settings()
    if not data:
        return {"configured": False, "enabled": False, "bridge_url": None, "masked_key": None}
    key = str(data.get("api_key") or "")
    return {
        "configured": bool(data.get("bridge_url") and key),
        "enabled": bool(data.get("enabled")),
        "bridge_url": data.get("bridge_url"),
        "masked_key": mask(key) if key else None,
    }


def delete_flow_settings() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM secure_settings WHERE key=?", (SETTING_KEY,))
