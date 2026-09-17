import shutil
from urllib.parse import quote, urlparse

from .crypto import decrypt, encrypt
from .db import connect

SETTING_KEY = "video_proxy"


def normalize_proxy(value: str) -> str:
    raw = value.strip().replace("\r", "").replace("\n", "")
    if not raw:
        raise ValueError("Proxy không được để trống")

    if "://" not in raw:
        # Dạng người dùng phổ biến: IP:PORT hoặc IP:PORT:USER:PASSWORD
        parts = raw.split(":", 3)
        if len(parts) == 2:
            host, port = (x.strip() for x in parts)
            raw = f"socks5h://{host}:{port}"
        elif len(parts) == 4:
            host, port, user, password = parts
            host, port, user, password = host.strip(), port.strip(), user.strip(), password.strip()
            if not user or not password:
                raise ValueError("Thiếu username hoặc password. Dùng IP:PORT:USER:PASSWORD")
            raw = f"socks5h://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        else:
            raise ValueError("Dán proxy theo dạng IP:PORT:USER:PASSWORD, ví dụ 1.2.3.4:1080:user:pass")

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"socks5", "socks5h"}:
        raise ValueError("Chỉ hỗ trợ SOCKS5 / SOCKS5H hoặc dạng IP:PORT:USER:PASSWORD")
    if not parsed.hostname:
        raise ValueError("Proxy thiếu IP/host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Port proxy không hợp lệ") from exc
    if not port or port < 1 or port > 65535:
        raise ValueError("Port proxy phải nằm trong khoảng 1-65535")
    return raw


def mask_proxy(proxy: str) -> str:
    parsed = urlparse(proxy)
    auth = ""
    if parsed.username:
        user = parsed.username[:2] + "•••" if len(parsed.username) > 2 else "•••"
        auth = f"{user}:••••••@"
    return f"{parsed.scheme}://{auth}{parsed.hostname}:{parsed.port}"


def save_video_proxy(proxy: str) -> str:
    normalized = normalize_proxy(proxy)
    with connect() as conn:
        conn.execute("""INSERT INTO secure_settings(key, encrypted_value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET encrypted_value=excluded.encrypted_value, updated_at=CURRENT_TIMESTAMP""",
        (SETTING_KEY, encrypt(normalized)))
    return normalized

def get_video_proxy() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT encrypted_value FROM secure_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    return decrypt(row["encrypted_value"]) if row else None


def get_video_proxy_status() -> dict:
    proxy = get_video_proxy()
    return {"configured": bool(proxy), "masked_proxy": mask_proxy(proxy) if proxy else None}


def delete_video_proxy() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM secure_settings WHERE key=?", (SETTING_KEY,))


def _youtube_probe(proxy: str) -> dict:
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 20, "proxy": proxy,
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
        },
    }
    node_path = shutil.which("node")
    if node_path:
        opts["js_runtimes"] = {"node": {"path": node_path}}
        opts["remote_components"] = {"ejs:github"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=jNQXAC9IVRw", download=False)
        return {"youtube_ok": True, "youtube_title": info.get("title") or "YouTube OK", "youtube_error": None}
    except Exception as exc:
        message = str(exc)
        if "Sign in to confirm" in message:
            message = "Proxy kết nối được nhưng IP proxy vẫn đang bị YouTube chặn."
        return {"youtube_ok": False, "youtube_title": None, "youtube_error": message[:600]}


async def test_video_proxy() -> dict:
    import asyncio
    import httpx

    proxy = get_video_proxy()
    if not proxy:
        raise ValueError("Chưa cấu hình proxy SOCKS5")
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=15, follow_redirects=True) as client:
            response = await client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            exit_ip = response.json().get("ip")
    except Exception as exc:
        return {
            "proxy_ok": False, "exit_ip": None, "proxy": mask_proxy(proxy),
            "youtube_ok": False, "youtube_title": None,
            "youtube_error": f"Không kết nối được qua SOCKS5: {exc}",
        }
    youtube = await asyncio.to_thread(_youtube_probe, proxy)
    return {"proxy_ok": True, "exit_ip": exit_ip, "proxy": mask_proxy(proxy), **youtube}
