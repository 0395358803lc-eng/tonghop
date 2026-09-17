import os
from cryptography.fernet import Fernet
from .config import KEY_PATH

def _load_master_key() -> bytes:
    env = os.getenv("SECRETS_ENCRYPTION_KEY")
    if env:
        return env.encode()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(0o600)
    return key

FERNET = Fernet(_load_master_key())

def encrypt(value: str) -> str:
    return FERNET.encrypt(value.encode()).decode()

def decrypt(value: str) -> str:
    return FERNET.decrypt(value.encode()).decode()

def mask(value: str) -> str:
    return f"{value[:5]}••••••{value[-4:]}" if len(value) > 12 else "••••••••"
