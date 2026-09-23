import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from cryptography.fernet import Fernet

from .config import KEY_PATH

CRYPTPROTECT_UI_FORBIDDEN = 0x1
DPAPI_SUFFIX = ".dpapi"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _desktop_dpapi_enabled() -> bool:
    return os.name == "nt" and os.getenv("TH_MEDIA_DESKTOP_MODE") == "1"


def _blob_from_bytes(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI chỉ khả dụng trên Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, keepalive = _blob_from_bytes(data)
    out_blob = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "TH Media Desktop",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    _ = keepalive
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI chỉ khả dụng trên Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, keepalive = _blob_from_bytes(data)
    out_blob = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    _ = keepalive
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_key_path() -> Path:
    return KEY_PATH.with_name(KEY_PATH.name + DPAPI_SUFFIX)


def _load_master_key() -> bytes:
    env = os.getenv("SECRETS_ENCRYPTION_KEY")
    if env:
        return env.encode()

    if _desktop_dpapi_enabled():
        protected_path = _dpapi_key_path()
        if protected_path.exists():
            return _dpapi_unprotect(protected_path.read_bytes()).strip()

        if KEY_PATH.exists():
            key = KEY_PATH.read_bytes().strip()
        else:
            key = Fernet.generate_key()

        protected_path.parent.mkdir(parents=True, exist_ok=True)
        protected_path.write_bytes(_dpapi_protect(key))
        try:
            protected_path.chmod(0o600)
        except OSError:
            pass

        if KEY_PATH.exists():
            KEY_PATH.unlink()
        return key

    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()

    key = Fernet.generate_key()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key)
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key


FERNET = Fernet(_load_master_key())


def encrypt(value: str) -> str:
    return FERNET.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return FERNET.decrypt(value.encode()).decode()


def mask(value: str) -> str:
    return f"{value[:5]}••••••{value[-4:]}" if len(value) > 12 else "••••••••"
