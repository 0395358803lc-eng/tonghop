from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .api import router
from .config import FRONTEND_DIST
from .db import init_db

app = FastAPI(title="TH Media", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

@app.on_event("startup")
def startup():
    init_db()
    try:
        from .film_recovery_service import reconcile_all
        reconcile_all()
    except Exception:
        pass

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
