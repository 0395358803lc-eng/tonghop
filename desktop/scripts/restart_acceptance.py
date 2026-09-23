from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
RELEASE_ROOT = PROJECT_ROOT / "desktop" / "src-tauri" / "target" / "release"
BACKEND_EXE = RELEASE_ROOT / "sidecars" / "th-media-backend" / "th-media-backend.exe"
FLOW_EXE = RELEASE_ROOT / "sidecars" / "th-media-flow-bridge" / "th-media-flow-bridge.exe"

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
ROOT = PROJECT_ROOT / "desktop" / ".restart-acceptance" / stamp
DB_DIR = ROOT / "Database"
MEDIA_DIR = ROOT / "Media"
TEMP_DIR = ROOT / "Temp"
FLOW_PROFILE = ROOT / "FlowProfile"
FLOW_SESSIONS = ROOT / "FlowSessions"
FLOW_REGISTRY = DB_DIR / "flow_sessions.json"
for folder in (DB_DIR, MEDIA_DIR, TEMP_DIR, FLOW_PROFILE, FLOW_SESSIONS):
    folder.mkdir(parents=True, exist_ok=True)

BASE_ENV = os.environ.copy()
BASE_ENV.update({
    "TH_MEDIA_DATA_DIR": str(ROOT),
    "TH_MEDIA_MEDIA_DIR": str(MEDIA_DIR),
    "TH_MEDIA_DB_PATH": str(DB_DIR / "aihub.db"),
    "TH_MEDIA_KEY_PATH": str(DB_DIR / "master.key"),
    "TH_MEDIA_TEMP_DIR": str(TEMP_DIR),
    "TH_MEDIA_FLOW_PROFILE_DIR": str(FLOW_PROFILE),
    "TH_MEDIA_FLOW_SESSIONS_DIR": str(FLOW_SESSIONS),
    "TH_MEDIA_FLOW_REGISTRY_PATH": str(FLOW_REGISTRY),
    "TH_MEDIA_FLOW_CONFIG_PATH": str(ROOT / "flow_bridge_config.json"),
    "TH_MEDIA_DESKTOP_MODE": "1",
    "TH_MEDIA_NETWORK_FORCE": "offline",
})

os.environ.update(BASE_ENV)
sys.path.insert(0, str(BACKEND_ROOT))

from app.db import init_db
from app.film_store import create_film_project, save_film_analysis
from app.film_media_store import project_media_root, register_completed_media
from app.film_render_store import create_render_jobs, list_render_jobs
from app.film_scene_state_store import create_run, ensure_scene_states, update_run, upsert_scene_state

init_db()
scene_id = "SCENE_RESTART_001"
project = create_film_project(
    "__restart_acceptance__",
    "Day la kich ban acceptance restart dai hon hai muoi ky tu va chi dung trong thu muc co lap.",
    "xkiro",
    "acceptance-model",
    {"scene_duration": 8, "aspect_ratio": "16:9", "resolution": "720p"},
)
project_id = project["id"]
analysis = {
    "project_title": "__restart_acceptance__",
    "master_prompt": "restart acceptance",
    "story_bible": {"logline": "restart acceptance"},
    "characters": [],
    "locations": [],
    "props": [],
    "visual_style": "test",
    "timeline": [],
    "source_manifest": {"acceptance": True},
    "integrity": {"final_gate": True, "gates": {"restart": True}, "errors": []},
    "scenes": [{
        "id": scene_id,
        "scene_index": 0,
        "title": "Restart scene",
        "duration": 8,
        "visual_prompt": "static acceptance scene",
        "start_state": {},
        "end_state": {},
        "dialogue": [],
    }],
}
save_film_analysis(project_id, analysis)

media_path = project_media_root(project_id) / "flow_downloads" / "restart-job" / "result.mp4"
media_path.parent.mkdir(parents=True, exist_ok=True)
media_path.write_bytes(b"restart-acceptance-media")
media = register_completed_media(
    project_id=project_id,
    media_type="video",
    role="scene_video",
    file_path=media_path,
    scene_id=scene_id,
    provider="flow",
    model="acceptance-model",
    provider_job_id="restart-provider-job",
    mime_type="video/mp4",
    qc_status="passed",
    qc_score=99,
    qc={"hard_gate": {"passed": True}},
    select_if_passed=True,
)
ensure_scene_states(project_id, analysis["scenes"])
upsert_scene_state(
    project_id,
    scene_id,
    0,
    status="APPROVED",
    selected_media_id=media["id"],
    current_job_id=None,
    force=True,
)
jobs = create_render_jobs(project_id, [scene_id], "flow")
run = create_run(project_id, gate={"final_gate": True})
update_run(run["id"], status="paused", error=None)

session_id = "restartsession"
profile_dir = FLOW_SESSIONS / session_id
(profile_dir / "Default").mkdir(parents=True, exist_ok=True)
(profile_dir / "Default" / "Preferences").write_text(
    json.dumps({"account_info": [{"email": "acceptance@example.invalid"}]}),
    encoding="utf-8",
)
FLOW_REGISTRY.write_text(
    json.dumps({
        "active_id": session_id,
        "sessions": [{
            "id": session_id,
            "name": "Restart Acceptance",
            "account_hint": "acceptance@example.invalid",
            "saved_at": "2026-09-24T00:00:00+00:00",
            "last_used_at": "2026-09-24T00:00:00+00:00",
            "profile_dir": str(profile_dir),
        }],
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

SEED = {
    "project_id": project_id,
    "scene_id": scene_id,
    "selected_media_id": media["id"],
    "job_ids": [item["id"] for item in list_render_jobs(project_id)],
    "run_id": run["id"],
    "flow_session_id": session_id,
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.15)
    raise RuntimeError(f"port {port} did not open")


def http_json(url: str, headers: dict[str, str] | None = None) -> dict | list:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {payload}") from exc


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def backend_snapshot() -> dict:
    port = free_port()
    token = "restart-acceptance-token"
    env = BASE_ENV.copy()
    env.update({
        "TH_MEDIA_AUTH_TOKEN": token,
        "TH_MEDIA_BACKEND_HOST": "127.0.0.1",
        "TH_MEDIA_BACKEND_PORT": str(port),
        "TH_MEDIA_BACKEND_LOG_LEVEL": "warning",
    })
    proc = subprocess.Popen(
        [str(BACKEND_EXE)],
        cwd=str(BACKEND_EXE.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        wait_port(port)
        headers = {"X-TH-Media-Token": token}
        project_now = http_json(f"http://127.0.0.1:{port}/api/film/projects/{project_id}", headers)
        media_now = http_json(f"http://127.0.0.1:{port}/api/film/projects/{project_id}/media", headers)
        render_now = http_json(f"http://127.0.0.1:{port}/api/film/projects/{project_id}/render", headers)
        pipeline_now = http_json(f"http://127.0.0.1:{port}/api/film/projects/{project_id}/pipeline", headers)
        selected = [
            item for item in (media_now.get("media") if isinstance(media_now, dict) else media_now)
            if item.get("is_selected")
        ]
        return {
            "project_id": project_now.get("id"),
            "final_gate": bool((project_now.get("integrity") or {}).get("final_gate")),
            "selected_media_ids": [item.get("id") for item in selected],
            "job_ids": [item.get("id") for item in (render_now.get("jobs") or [])],
            "run_id": ((pipeline_now.get("run") or {}).get("id")),
            "run_status": ((pipeline_now.get("run") or {}).get("status")),
        }
    finally:
        kill_tree(proc)


def flow_snapshot() -> dict:
    port = free_port()
    key = "restart-flow-key"
    env = BASE_ENV.copy()
    env.update({
        "FLOW_BRIDGE_API_KEY": key,
        "TH_MEDIA_FLOW_BRIDGE_HOST": "127.0.0.1",
        "TH_MEDIA_FLOW_BRIDGE_PORT": str(port),
        "TH_MEDIA_FLOW_BRIDGE_LOG_LEVEL": "warning",
    })
    proc = subprocess.Popen(
        [str(FLOW_EXE)],
        cwd=str(FLOW_EXE.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        wait_port(port)
        data = json.loads(FLOW_REGISTRY.read_text(encoding="utf-8"))
        return {
            "sidecar_port_open": True,
            "active_id": data.get("active_id"),
            "session_ids": [item.get("id") for item in (data.get("sessions") or [])],
            "profile_exists": profile_dir.is_dir(),
        }
    finally:
        kill_tree(proc)


first_backend = backend_snapshot()
first_flow = flow_snapshot()
second_backend = backend_snapshot()
second_flow = flow_snapshot()

checks = {
    "project_persisted": first_backend["project_id"] == project_id == second_backend["project_id"],
    "final_gate_persisted": first_backend["final_gate"] and second_backend["final_gate"],
    "selected_media_persisted": (
        SEED["selected_media_id"] in first_backend["selected_media_ids"]
        and first_backend["selected_media_ids"] == second_backend["selected_media_ids"]
    ),
    "queue_not_duplicated": (
        sorted(first_backend["job_ids"]) == sorted(SEED["job_ids"])
        and sorted(second_backend["job_ids"]) == sorted(SEED["job_ids"])
    ),
    "pipeline_run_persisted": (
        first_backend["run_id"] == SEED["run_id"] == second_backend["run_id"]
        and first_backend["run_status"] == "paused"
        and second_backend["run_status"] == "paused"
    ),
    "flow_session_persisted": (
        first_flow["active_id"] == session_id == second_flow["active_id"]
        and session_id in first_flow["session_ids"]
        and first_flow["session_ids"] == second_flow["session_ids"]
        and first_flow["profile_exists"]
        and second_flow["profile_exists"]
    ),
}

result = {
    "ok": all(checks.values()),
    "root": str(ROOT),
    "seed": SEED,
    "first_backend": first_backend,
    "second_backend": second_backend,
    "first_flow": first_flow,
    "second_flow": second_flow,
    "checks": checks,
    "note": "Flow acceptance verifies saved session registry/profile persistence, not live Google cookie validity.",
}
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result["ok"]:
    raise SystemExit(1)
