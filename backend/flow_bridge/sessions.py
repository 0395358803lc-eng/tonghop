import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .config import DATA_DIR, load_config

ACTIVE_PROFILE = Path(os.getenv("TH_MEDIA_FLOW_PROFILE_DIR") or (DATA_DIR / "flow_chrome_profile")).resolve()
SESSIONS_ROOT = Path(os.getenv("TH_MEDIA_FLOW_SESSIONS_DIR") or (DATA_DIR / "flow_sessions")).resolve()
REGISTRY_PATH = Path(os.getenv("TH_MEDIA_FLOW_REGISTRY_PATH") or (DATA_DIR / "flow_sessions.json")).resolve()
SKIP_DIRS = {
    "Crashpad", "ShaderCache", "GrShaderCache", "GraphiteDawnCache",
    "Code Cache", "GPUCache", "DawnGraphiteCache",
}
SKIP_FILES = {
    "SingletonLock", "SingletonCookie", "SingletonSocket",
    "lockfile", "RunningChromeVersion", "DevToolsActivePort",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chrome_exe() -> Path:
    configured = os.getenv("TH_MEDIA_CHROME_PATH")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    candidates = [path for path in candidates if path is not None]
    chrome = next((path for path in candidates if path.exists()), None)
    if chrome is None:
        raise RuntimeError("Không tìm thấy Google Chrome trên máy.")
    return chrome


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"active_id": None, "sessions": []}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    return {"active_id": data.get("active_id"), "sessions": sessions}


def save_registry(data: dict) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def account_hint(profile: Path | None = None) -> str | None:
    prefs = (profile or ACTIVE_PROFILE) / "Default" / "Preferences"
    if not prefs.exists():
        return None
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
    except Exception:
        return None
    info = data.get("account_info")
    if isinstance(info, list) and info:
        first = info[0] if isinstance(info[0], dict) else {}
        return first.get("email") or first.get("full_name") or first.get("given_name")
    google = data.get("google") if isinstance(data.get("google"), dict) else {}
    services = google.get("services") if isinstance(google.get("services"), dict) else {}
    return services.get("last_signed_in_username") or None


def _copy_profile(src: Path, dst: Path) -> None:
    if not src.exists():
        raise RuntimeError("Chưa có Chrome profile Flow để lưu.")
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os_walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            if name in SKIP_FILES or name.endswith(".lock"):
                continue
            source = Path(root) / name
            try:
                shutil.copy2(source, target_dir / name)
            except Exception:
                continue


def os_walk(src: Path):
    import os
    return os.walk(src)


def chrome_pids(profile: Path | None = None) -> list[int]:
    needle = str(profile or ACTIVE_PROFILE).replace("'", "''")
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return []
    pids = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def set_flow_chrome_visibility(visible: bool) -> int:
    """Show or hide only top-level Chrome windows that belong to the dedicated Flow profile."""
    if __import__("os").name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        target_pids = set(chrome_pids())
        if not target_pids:
            return 0

        user32 = ctypes.windll.user32
        shown = 0
        SW_HIDE = 0
        SW_RESTORE = 9

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            nonlocal shown
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in target_pids:
                return True
            class_name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            if class_name.value != "Chrome_WidgetWin_1":
                return True
            user32.ShowWindow(hwnd, SW_RESTORE if visible else SW_HIDE)
            shown += 1
            return True

        user32.EnumWindows(enum_proc, 0)
        return shown
    except Exception:
        return 0


def cdp_port() -> int:
    raw = str(load_config().get("cdp_url") or "")
    try:
        parsed = urlparse(raw)
        if parsed.port:
            return int(parsed.port)
    except ValueError:
        pass
    return 9223


def port_open(port: int | None = None) -> bool:
    target_port = int(port or cdp_port())
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", target_port)) == 0


def wait_port(port: int, seconds: float, want_open: bool) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_open(port) is want_open:
            return True
        time.sleep(0.35)
    return port_open(port) is want_open


def stop_flow_chrome() -> None:
    port = cdp_port()
    for pid in chrome_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    wait_port(port, 12, want_open=False)
    lock = ACTIVE_PROFILE / "SingletonLock"
    if lock.exists():
        try:
            lock.unlink()
        except Exception:
            pass


def start_flow_chrome(url: str | None = None, *, visible: bool = False) -> None:
    ACTIVE_PROFILE.mkdir(parents=True, exist_ok=True)
    port = cdp_port()
    if port_open(port):
        set_flow_chrome_visibility(visible)
        return

    args = [
        str(_chrome_exe()),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={ACTIVE_PROFILE}",
        "--restore-last-session",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized" if visible else "--start-minimized",
    ]
    if url:
        args.append(url)

    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    if not wait_port(port, 20, want_open=True):
        raise RuntimeError(f"Chrome Flow không mở lại được cổng {port}.")
    time.sleep(1.0)
    set_flow_chrome_visibility(visible)


def list_sessions() -> dict:
    registry = load_registry()
    hint = account_hint()
    return {
        "ok": True,
        "active_id": registry.get("active_id"),
        "active_account": hint,
        "sessions": registry.get("sessions") or [],
    }


def save_current_session(name: str | None = None) -> dict:
    stop_flow_chrome()
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    hint = account_hint()
    label = (name or "").strip() or hint or f"Phiên {datetime.now().strftime('%d/%m %H:%M')}"
    existing = next(
        (item for item in registry["sessions"] if item.get("id") == registry.get("active_id") or item.get("name") == label or item.get("account_hint") == hint),
        None,
    )
    session_id = str(existing["id"]) if existing else uuid.uuid4().hex[:12]
    dest = SESSIONS_ROOT / session_id
    _copy_profile(ACTIVE_PROFILE, dest)
    entry = {
        "id": session_id,
        "name": label,
        "account_hint": hint,
        "saved_at": _now(),
        "last_used_at": _now(),
        "profile_dir": str(dest),
    }
    sessions = [item for item in registry["sessions"] if item.get("id") != session_id]
    sessions.insert(0, entry)
    registry = save_registry({"active_id": session_id, "sessions": sessions})
    start_flow_chrome()
    return {"ok": True, "saved": entry, **list_sessions(), "message": f"Đã lưu phiên {label}."}


def new_session(save_current: bool = True, name: str | None = None) -> dict:
    stop_flow_chrome()
    saved = None
    registry = load_registry()
    if save_current and ACTIVE_PROFILE.exists() and any(ACTIVE_PROFILE.iterdir()):
        SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
        hint = account_hint()
        label = (name or "").strip() or hint or f"Phiên {datetime.now().strftime('%d/%m %H:%M')}"
        existing = next(
            (item for item in registry["sessions"] if item.get("id") == registry.get("active_id") or item.get("name") == label or (hint and item.get("account_hint") == hint)),
            None,
        )
        session_id = str(existing["id"]) if existing else uuid.uuid4().hex[:12]
        dest = SESSIONS_ROOT / session_id
        try:
            _copy_profile(ACTIVE_PROFILE, dest)
            saved = {
                "id": session_id,
                "name": label,
                "account_hint": hint,
                "saved_at": _now(),
                "last_used_at": _now(),
                "profile_dir": str(dest),
            }
            sessions = [item for item in registry["sessions"] if item.get("id") != session_id]
            sessions.insert(0, saved)
            registry = {"active_id": None, "sessions": sessions}
            save_registry(registry)
        except Exception:
            saved = None
    if ACTIVE_PROFILE.exists():
        shutil.rmtree(ACTIVE_PROFILE, ignore_errors=True)
    ACTIVE_PROFILE.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    registry["active_id"] = None
    save_registry(registry)
    start_flow_chrome("https://accounts.google.com/ServiceLogin?continue=https://flow.google.com/", visible=True)
    return {
        "ok": True,
        "saved_previous": saved,
        **list_sessions(),
        "message": "Đã mở phiên mới. Đăng nhập tài khoản Google khác trong cửa sổ Chrome vừa mở.",
    }


def restore_session(session_id: str) -> dict:
    registry = load_registry()
    entry = next((item for item in registry["sessions"] if item.get("id") == session_id), None)
    if not entry:
        raise RuntimeError("Không tìm thấy phiên đã lưu.")
    source = Path(entry["profile_dir"]) if entry.get("profile_dir") else SESSIONS_ROOT / session_id
    if not source.exists():
        raise RuntimeError("Thư mục phiên đã lưu không còn trên máy.")
    stop_flow_chrome()
    if ACTIVE_PROFILE.exists():
        shutil.rmtree(ACTIVE_PROFILE, ignore_errors=True)
    _copy_profile(source, ACTIVE_PROFILE)
    entry["last_used_at"] = _now()
    sessions = [entry] + [item for item in registry["sessions"] if item.get("id") != session_id]
    save_registry({"active_id": session_id, "sessions": sessions})
    start_flow_chrome()
    return {
        "ok": True,
        "restored": entry,
        **list_sessions(),
        "message": f"Đã chuyển sang phiên {entry.get('name') or session_id}.",
    }


def delete_session(session_id: str) -> dict:
    registry = load_registry()
    entry = next((item for item in registry["sessions"] if item.get("id") == session_id), None)
    if not entry:
        raise RuntimeError("Không tìm thấy phiên đã lưu.")
    folder = Path(entry["profile_dir"]) if entry.get("profile_dir") else SESSIONS_ROOT / session_id
    shutil.rmtree(folder, ignore_errors=True)
    sessions = [item for item in registry["sessions"] if item.get("id") != session_id]
    active_id = None if registry.get("active_id") == session_id else registry.get("active_id")
    save_registry({"active_id": active_id, "sessions": sessions})
    return {"ok": True, **list_sessions(), "message": f"Đã xóa phiên {entry.get('name') or session_id}."}
