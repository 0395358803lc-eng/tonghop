from .crypto import decrypt, encrypt, mask
from .db import connect

def save_provider(provider: str, api_key: str, base_url: str | None = None):
    encrypted = encrypt(api_key.strip())
    with connect() as conn:
        conn.execute("""INSERT INTO provider_keys(provider, encrypted_key, base_url) VALUES(?,?,?)
        ON CONFLICT(provider) DO UPDATE SET encrypted_key=excluded.encrypted_key, base_url=excluded.base_url, updated_at=CURRENT_TIMESTAMP""",
        (provider, encrypted, base_url))

def delete_provider(provider: str):
    with connect() as conn:
        conn.execute("DELETE FROM provider_keys WHERE provider=?", (provider,))

def get_provider(provider: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM provider_keys WHERE provider=?", (provider,)).fetchone()
    if not row:
        return None
    return {"api_key": decrypt(row["encrypted_key"]), "base_url": row["base_url"]}

def list_saved():
    with connect() as conn:
        rows = conn.execute("SELECT provider, encrypted_key, base_url FROM provider_keys").fetchall()
    return {r["provider"]: {"masked_key": mask(decrypt(r["encrypted_key"])), "base_url": r["base_url"]} for r in rows}
