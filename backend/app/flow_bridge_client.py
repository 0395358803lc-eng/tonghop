import asyncio
import time

import httpx

from .flow_store import get_flow_settings


class FlowBridgeError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    cfg = get_flow_settings()
    if not cfg or not cfg.get("bridge_url") or not cfg.get("api_key"):
        raise FlowBridgeError("Flow Bridge chưa được cấu hình.")
    return str(cfg["bridge_url"]).rstrip("/"), str(cfg["api_key"])


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _raise_for_bridge_response(response: httpx.Response, *, session_detail: str = "Flow session chưa đăng nhập.") -> None:
    if response.status_code == 409:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text
        raise FlowBridgeError(f"SESSION_EXPIRED: {detail or session_detail}")
    if response.status_code == 401:
        raise FlowBridgeError("BRIDGE_AUTH_ERROR: Flow Bridge API key không hợp lệ.")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FlowBridgeError(
            f"FLOW_BRIDGE_HTTP_{response.status_code}: {response.text[:1000]}"
        ) from exc


async def test_flow_bridge() -> dict:
    base_url, api_key = _credentials()
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await client.get(f"{base_url}/v1/health", headers=_headers(api_key))
        health.raise_for_status()
        session = await client.get(f"{base_url}/v1/session", headers=_headers(api_key))
        session.raise_for_status()
    health_data = health.json()
    session_data = session.json()
    return {
        "ok": True,
        "bridge": health_data,
        "session": session_data,
        "authenticated": bool(session_data.get("authenticated")),
    }


async def open_flow_login() -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{base_url}/v1/session/open-login", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def list_flow_sessions() -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{base_url}/v1/sessions", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def save_flow_session(name: str | None = None) -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(f"{base_url}/v1/sessions/save", headers=_headers(api_key), json={"name": name})
        response.raise_for_status()
        return response.json()


async def new_flow_session(save_current: bool = True, name: str | None = None) -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            f"{base_url}/v1/sessions/new",
            headers=_headers(api_key),
            json={"save_current": save_current, "name": name},
        )
        response.raise_for_status()
        return response.json()


async def restore_flow_session(session_id: str) -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(f"{base_url}/v1/sessions/{session_id}/restore", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def delete_flow_session(session_id: str) -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(f"{base_url}/v1/sessions/{session_id}", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def get_flow_metrics() -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(f"{base_url}/v1/metrics", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def get_flow_projects() -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(f"{base_url}/v1/projects", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def get_flow_video_capabilities(project_id: str | None = None) -> dict:
    base_url, api_key = _credentials()
    params = {"project_id": project_id} if project_id else None
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(f"{base_url}/v1/capabilities/video", headers=_headers(api_key), params=params)
        response.raise_for_status()
        return response.json()


async def get_flow_image_capabilities(project_id: str | None = None) -> dict:
    base_url, api_key = _credentials()
    params = {"project_id": project_id} if project_id else None
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(f"{base_url}/v1/capabilities/image", headers=_headers(api_key), params=params)
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise FlowBridgeError(f"SESSION_EXPIRED: {detail or 'Flow session chưa đăng nhập.'}")
        response.raise_for_status()
        data = response.json()
        models = data.get("models")
        if isinstance(models, list):
            data["models"] = _filter_image_models(models)
        return data


def _filter_image_models(models: list) -> list:
    import re
    out = []
    seen = set()
    for item in models:
        name = str(item or "").strip()
        if not name:
            continue
        if re.search(r"crop_\d+_\d+", name, re.I):
            continue
        if re.search(r"\sx\d+$", name, re.I):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


async def render_flow_image(payload: dict) -> dict:
    base_url, api_key = _credentials()
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/v1/generations/image",
            headers=_headers(api_key),
            json=payload,
        )
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise FlowBridgeError(f"SESSION_EXPIRED: {detail or 'Flow session chưa đăng nhập.'}")
        if response.status_code == 401:
            raise FlowBridgeError("BRIDGE_AUTH_ERROR: Flow Bridge API key không hợp lệ.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FlowBridgeError(
                f"FLOW_BRIDGE_HTTP_{response.status_code}: {response.text[:1000]}"
            ) from exc
        data = response.json()

    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise FlowBridgeError("Flow Bridge không trả image job_id.")

    timeout_seconds = int(data.get("timeout_seconds") or 600)
    deadline = time.monotonic() + timeout_seconds
    job = data
    while time.monotonic() < deadline:
        status = str(job.get("status") or "").lower()
        if status in {"completed", "succeeded", "success"}:
            break
        if status in {"failed", "error", "blocked", "cancelled", "canceled"}:
            code = str(job.get("error_code") or "FLOW_IMAGE_JOB_FAILED")
            message = str(job.get("error") or job.get("message") or f"Flow image job {status}.")
            raise FlowBridgeError(f"{code}: {message}")
        await asyncio.sleep(2)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/v1/jobs/{job_id}", headers=_headers(api_key))
            _raise_for_bridge_response(
                response,
                session_detail="Flow session hết hạn trong lúc đang chờ generation.",
            )
            job = response.json()
    else:
        raise FlowBridgeError(f"Flow image job {job_id} hết thời gian chờ.")

    result_url = str(job.get("result_url") or "")
    if not result_url:
        raise FlowBridgeError("Flow image job hoàn tất nhưng không có file URL.")
    file_url = result_url if result_url.startswith("http") else f"{base_url}{result_url}"
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        response = await client.get(file_url, headers=_headers(api_key))
        response.raise_for_status()
        image_bytes = response.content
    if not image_bytes:
        raise FlowBridgeError("Flow image file rỗng.")

    return {
        "provider_job_id": str(job_id),
        "image_bytes": image_bytes,
        "flow_project_id": job.get("flow_project_id"),
        "media_id": job.get("media_id"),
        "source_url": job.get("source_url"),
        "model": job.get("model") or (job.get("effective_settings") or {}).get("model"),
        "aspect_ratio": job.get("aspect_ratio") or (job.get("effective_settings") or {}).get("aspect_ratio"),
        "raw": job,
    }


async def render_flow_video(payload: dict) -> dict:
    base_url, api_key = _credentials()
    submit_timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(timeout=submit_timeout) as client:
        response = await client.post(
            f"{base_url}/v1/generations/video",
            headers=_headers(api_key),
            json=payload,
        )
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise FlowBridgeError(f"SESSION_EXPIRED: {detail or 'Flow session chưa đăng nhập.'}")
        if response.status_code == 401:
            raise FlowBridgeError("BRIDGE_AUTH_ERROR: Flow Bridge API key không hợp lệ.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FlowBridgeError(
                f"FLOW_BRIDGE_HTTP_{response.status_code}: {response.text[:1000]}"
            ) from exc
        data = response.json()

    result_url = data.get("result_url") or data.get("video_url") or data.get("url")
    if result_url:
        return _normalize_result(data, result_url)

    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise FlowBridgeError("Flow Bridge không trả job_id hoặc result_url.")

    timeout_seconds = int(data.get("timeout_seconds") or 900)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{base_url}/v1/jobs/{job_id}", headers=_headers(api_key))
            _raise_for_bridge_response(
                response,
                session_detail="Flow session hết hạn trong lúc đang chờ generation.",
            )
            job = response.json()
        status = str(job.get("status") or "").lower()
        if status in {"completed", "succeeded", "success"}:
            result_url = job.get("result_url") or job.get("video_url") or job.get("url")
            if not result_url:
                raise FlowBridgeError("Flow job hoàn tất nhưng không có URL video.")
            return _normalize_result(job, result_url, str(job_id))
        if status in {"failed", "error", "blocked", "cancelled", "canceled"}:
            code = str(job.get("error_code") or "FLOW_JOB_FAILED")
            message = str(job.get("error") or job.get("message") or f"Flow job {status}.")
            raise FlowBridgeError(f"{code}: {message}")
    raise FlowBridgeError(f"Flow job {job_id} hết thời gian chờ.")


def _normalize_result(data: dict, result_url: str, job_id: str | None = None) -> dict:
    return {
        "provider_job_id": job_id or data.get("job_id") or data.get("id"),
        "result_url": result_url,
        "first_frame_url": data.get("first_frame_url"),
        "last_frame_url": data.get("last_frame_url"),
        "raw": data,
    }
