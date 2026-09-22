import hmac
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .api import router
from .config import FRONTEND_DIST
from .db import init_db

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv("TH_MEDIA_ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)).split(",")
    if value.strip()
]

app = FastAPI(title="TH Media", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

def _desktop_token_valid(supplied: str | None) -> bool:
    expected = os.getenv("TH_MEDIA_AUTH_TOKEN") or ""
    if not expected:
        return True
    return bool(supplied and hmac.compare_digest(supplied, expected))


@app.middleware("http")
async def desktop_runtime_auth(request: Request, call_next):
    if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
        if not _desktop_token_valid(request.headers.get("X-TH-Media-Token")):
            return JSONResponse(status_code=401, content={"detail": "TH Media Desktop token không hợp lệ."})
    return await call_next(request)


@app.on_event("startup")
def startup():
    init_db()
    bridge_url = os.getenv("TH_MEDIA_FLOW_BRIDGE_URL")
    bridge_key = os.getenv("FLOW_BRIDGE_API_KEY")
    if bridge_url and bridge_key:
        try:
            from .flow_store import save_flow_settings
            save_flow_settings(bridge_url, bridge_key, True)
        except Exception:
            pass
    try:
        from .film_recovery_service import reconcile_all
        reconcile_all()
    except Exception:
        pass

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
