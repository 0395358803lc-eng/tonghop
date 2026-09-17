from uuid import uuid4
from .db import connect

def create_chat(provider: str, model: str):
    chat_id = str(uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO chats(id,title,provider,model) VALUES(?,?,?,?)", (chat_id, "Cuộc trò chuyện mới", provider, model))
    return get_chat(chat_id)

def list_chats():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]

def get_chat(chat_id: str):
    with connect() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        messages = conn.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at,id", (chat_id,)).fetchall()
    if not chat:
        return None
    data = dict(chat)
    data["messages"] = [dict(m) for m in messages]
    return data

def update_chat(chat_id: str, **values):
    allowed = {k: v for k, v in values.items() if k in {"provider", "model", "title"} and v is not None}
    if allowed:
        clause = ", ".join(f"{k}=?" for k in allowed)
        with connect() as conn:
            conn.execute(f"UPDATE chats SET {clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*allowed.values(), chat_id))
    return get_chat(chat_id)

def add_message(chat_id: str, role: str, content: str):
    message_id = str(uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO messages(id,chat_id,role,content) VALUES(?,?,?,?)", (message_id, chat_id, role, content))
        if role == "user":
            count = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND role='user'", (chat_id,)).fetchone()[0]
            if count == 1:
                title = " ".join(content.strip().split())[:56] or "Cuộc trò chuyện mới"
                conn.execute("UPDATE chats SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (title, chat_id))
            else:
                conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
    return message_id

def delete_chat(chat_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
