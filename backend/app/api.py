import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from .chat_store import add_message, create_chat, delete_chat, get_chat, list_chats, update_chat
from .provider_store import delete_provider, get_provider, list_saved, save_provider
from .providers.models import list_models
from .providers.registry import DEFAULT_MODELS, PROVIDERS
from .providers.service import run_chat
from .video_service import detect_platform, process_video_job
from .vision_service import delete_video_artifacts, get_frame_path
from .video_store import create_video_job, delete_video_job, get_video_job, list_video_jobs
from .video_proxy_store import delete_video_proxy, get_video_proxy_status, save_video_proxy, test_video_proxy
from .film_store import create_film_project, delete_film_project, get_film_project, list_film_projects, update_film_project, update_film_scene
from .film_service import audit_film_continuity, process_film_project
from .film_render_service import enqueue_render, pause_render_queue, process_render_queue, render_status, resume_render_queue, retry_render
from .schemas import ChatCreate, ChatUpdate, MessageCreate, ProviderKeyIn, VideoAnalyzeIn, VideoProxyIn, FilmProjectCreate, FilmProjectUpdate, FilmSceneUpdate, FilmRenderQueueIn

router = APIRouter(prefix="/api")

@router.get("/health")
def health():
    return {"ok": True, "service": "TH Media"}

@router.get("/providers")
def providers():
    saved = list_saved()
    result = []
    for key, cfg in PROVIDERS.items():
        item = {"id": key, **cfg, "configured": key in saved}
        item["masked_key"] = saved.get(key, {}).get("masked_key")
        item["custom_base_url"] = saved.get(key, {}).get("base_url")
        result.append(item)
    return result

@router.put("/providers/{provider}")
def put_provider(provider: str, body: ProviderKeyIn):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    save_provider(provider, body.api_key, body.base_url)
    return {"ok": True}

@router.delete("/providers/{provider}")
def remove_provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    delete_provider(provider)
    return {"ok": True}

@router.get("/providers/{provider}/models")
async def provider_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Nhà cung cấp không tồn tại")
    saved = get_provider(provider)
    if not saved and provider != "xkiro":
        return {"models": DEFAULT_MODELS.get(provider, []), "configured": False}
    cfg = saved or {"api_key": None, "base_url": None}
    models = await list_models(provider, cfg["api_key"], cfg.get("base_url"))
    return {"models": models, "configured": bool(saved)}

@router.get("/film/projects")
def film_projects():
    return list_film_projects()

@router.post("/film/projects")
def new_film_project(body: FilmProjectCreate):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    if not get_provider(body.provider):
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")
    return create_film_project(body.name, body.original_text, body.provider, body.model, body.settings)

@router.get("/film/projects/{project_id}")
def film_project_detail(project_id: str):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.patch("/film/projects/{project_id}")
def patch_film_project(project_id: str, body: FilmProjectUpdate):
    project = update_film_project(project_id, body.name, body.settings)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.delete("/film/projects/{project_id}")
def remove_film_project(project_id: str):
    delete_film_project(project_id)
    return {"ok": True}

@router.post("/film/projects/{project_id}/analyze")
def analyze_film_project(project_id: str, background_tasks: BackgroundTasks):
    project = get_film_project(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    if project["status"] == "analyzing":
        return project
    background_tasks.add_task(process_film_project, project_id)
    return {**project, "status": "queued", "stage": "Đang xếp hàng phân tích", "progress": 1}

@router.patch("/film/projects/{project_id}/scenes/{scene_id}")
def patch_film_scene(project_id: str, scene_id: str, body: FilmSceneUpdate):
    project = update_film_scene(project_id, scene_id, body.model_dump())
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.post("/film/projects/{project_id}/continuity-check")
def continuity_check(project_id: str):
    project = audit_film_continuity(project_id)
    if not project:
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return project

@router.get("/film/projects/{project_id}/render")
def film_render_status(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    return render_status(project_id)

@router.post("/film/projects/{project_id}/render/queue")
def queue_film_render(project_id: str, body: FilmRenderQueueIn, background_tasks: BackgroundTasks):
    try:
        jobs = enqueue_render(project_id, body.scene_ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    background_tasks.add_task(process_render_queue, project_id)
    return {"jobs": jobs, **render_status(project_id)}

@router.post("/film/projects/{project_id}/render/pause")
def pause_film_render(project_id: str):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    pause_render_queue(project_id)
    return render_status(project_id)

@router.post("/film/projects/{project_id}/render/resume")
def resume_film_render(project_id: str, background_tasks: BackgroundTasks):
    if not get_film_project(project_id):
        raise HTTPException(404, "Không tìm thấy dự án phim")
    resume_render_queue(project_id)
    background_tasks.add_task(process_render_queue, project_id)
    return render_status(project_id)

@router.post("/film/render/jobs/{job_id}/retry")
def retry_film_render(job_id: str, background_tasks: BackgroundTasks):
    job = retry_render(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy render job")
    background_tasks.add_task(process_render_queue, job["project_id"])
    return job

@router.get("/video/proxy")
def video_proxy_status():
    return get_video_proxy_status()

@router.put("/video/proxy")
def put_video_proxy(body: VideoProxyIn):
    try:
        proxy = save_video_proxy(body.proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = get_video_proxy_status()
    result["ok"] = True
    return result

@router.delete("/video/proxy")
def remove_video_proxy():
    delete_video_proxy()
    return {"ok": True}

@router.post("/video/proxy/test")
async def check_video_proxy():
    try:
        return await test_video_proxy()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@router.get("/video/jobs")
def video_jobs():
    return list_video_jobs()

@router.post("/video/jobs")
async def new_video_job(body: VideoAnalyzeIn, background_tasks: BackgroundTasks):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    if not get_provider(body.provider):
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")
    try:
        platform = detect_platform(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = create_video_job(body.url.strip(), platform, body.provider, body.model)
    background_tasks.add_task(process_video_job, job["id"])
    return job

@router.get("/video/jobs/{job_id}")
def video_job(job_id: str):
    job = get_video_job(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy tác vụ phân tích")
    return job

@router.get("/video/jobs/{job_id}/frames/{filename}")
def video_frame(job_id: str, filename: str):
    if not get_video_job(job_id):
        raise HTTPException(404, "Không tìm thấy tác vụ phân tích")
    path = get_frame_path(job_id, filename)
    if not path:
        raise HTTPException(404, "Không tìm thấy keyframe")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

@router.delete("/video/jobs/{job_id}")
def remove_video_job(job_id: str):
    delete_video_artifacts(job_id)
    delete_video_job(job_id)
    return {"ok": True}

@router.get("/chats")
def chats():
    return list_chats()

@router.post("/chats")
def new_chat(body: ChatCreate):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Nhà cung cấp không hợp lệ")
    return create_chat(body.provider, body.model)

@router.get("/chats/{chat_id}")
def chat_detail(chat_id: str):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    return chat

@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, body: ChatUpdate):
    chat = update_chat(chat_id, **body.model_dump())
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    return chat

@router.delete("/chats/{chat_id}")
def remove_chat(chat_id: str):
    delete_chat(chat_id)
    return {"ok": True}

@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageCreate):
    chat = get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
    credentials = get_provider(chat["provider"])
    if not credentials:
        raise HTTPException(400, "Hãy cấu hình API key cho nhà cung cấp này trước")
    add_message(chat_id, "user", body.content.strip())
    current = get_chat(chat_id)
    context = [{"role": m["role"], "content": m["content"]} for m in current["messages"]]
    try:
        answer = await run_chat(chat["provider"], credentials["api_key"], credentials.get("base_url"), chat["model"], context)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise HTTPException(exc.response.status_code, f"API provider trả lỗi: {detail}") from exc
    except Exception as exc:
        raise HTTPException(502, f"Không thể gọi model: {exc}") from exc
    add_message(chat_id, "assistant", answer)
    return get_chat(chat_id)
