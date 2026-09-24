import asyncio
import hmac
import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .api import router
from .config import FRONTEND_DIST
from .db import init_db
from .desktop_network import internet_status, requires_internet

DESKTOP_ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
DEV_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    *DESKTOP_ALLOWED_ORIGINS,
]


def _configured_allowed_origins() -> list[str]:
    if os.getenv("TH_MEDIA_DESKTOP_MODE") == "1":
        if os.getenv("TH_MEDIA_DEV_SERVER") == "1":
            return list(DEV_ALLOWED_ORIGINS)
        return list(DESKTOP_ALLOWED_ORIGINS)
    return [
        value.strip()
        for value in os.getenv("TH_MEDIA_ALLOWED_ORIGINS", ",".join(DEV_ALLOWED_ORIGINS)).split(",")
        if value.strip()
    ]


ALLOWED_ORIGINS = _configured_allowed_origins()

app = FastAPI(title="TH Media", version="0.1.0")
_recovery_supervisor_task: asyncio.Task | None = None
_request_logger = logging.getLogger("uvicorn.error")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-TH-Media-Token", "X-Request-ID", "Range"],
    expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges", "Content-Length"],
)
app.include_router(router)

def _desktop_token_valid(supplied: str | None) -> bool:
    expected = os.getenv("TH_MEDIA_AUTH_TOKEN") or ""
    desktop_mode = os.getenv("TH_MEDIA_DESKTOP_MODE") == "1"
    if not expected:
        return not desktop_mode
    return bool(supplied and hmac.compare_digest(supplied, expected))


@app.middleware("http")
async def desktop_runtime_auth(request: Request, call_next):
    request_id = (request.headers.get("X-Request-ID") or str(uuid.uuid4())).strip()
    request.state.request_id = request_id
    if request.method != "OPTIONS" and request.url.path.startswith("/api/"):
        if not _desktop_token_valid(request.headers.get("X-TH-Media-Token")):
            response = JSONResponse(status_code=401, content={"detail": "TH Media Desktop token không hợp lệ."})
            response.headers["X-Request-ID"] = request_id
            return response
        if requires_internet(request.method, request.url.path):
            network = await asyncio.to_thread(internet_status)
            if not network.get("online"):
                response = JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Chưa kết nối Internet. Dự án và dữ liệu local vẫn sử dụng được; hãy thử lại khi mạng trở lại.",
                        "code": "INTERNET_OFFLINE",
                        "network": network,
                    },
                )
                response.headers["X-Request-ID"] = request_id
                return response
    try:
        response = await call_next(request)
    except Exception:
        _request_logger.exception(
            "request_failed request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    if response.status_code >= 500:
        _request_logger.error(
            "request_error request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
    return response


@app.on_event("startup")
async def startup():
    global _recovery_supervisor_task
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
        from .film_recovery_service import reconcile_all, startup_recovery_supervisor
        reconcile_all()
        if _recovery_supervisor_task is None or _recovery_supervisor_task.done():
            _recovery_supervisor_task = asyncio.create_task(
                startup_recovery_supervisor(),
                name="th-media-recovery-supervisor",
            )
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown():
    global _recovery_supervisor_task
    task = _recovery_supervisor_task
    _recovery_supervisor_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
