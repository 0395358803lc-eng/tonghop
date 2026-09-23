import asyncio
import html
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp

from .provider_store import get_provider
from .providers.service import run_chat
from .video_store import get_video_job, update_video_job
from .video_proxy_store import get_video_proxy
from runtime_dependencies import whisper_model_path

from .vision_service import analyze_keyframes, extract_keyframes, keyframes_json

SUPPORTED = {
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.watch"),
}
MAX_DURATION = int(os.getenv("VIDEO_MAX_DURATION_SECONDS", "7200"))
WHISPER_MODEL = os.getenv("VIDEO_WHISPER_MODEL", "base")
ENV_VIDEO_PROXY = os.getenv("VIDEO_PROXY")
VIDEO_COOKIES_FILE = os.getenv("VIDEO_COOKIES_FILE")
_whisper = None


def detect_platform(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Link video không hợp lệ")
    host = parsed.hostname.lower().removeprefix("www.")
    for platform, domains in SUPPORTED.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    raise ValueError("Chỉ hỗ trợ link YouTube, TikTok hoặc Facebook")


def _ydl_options(download: bool = False, output: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": not download,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "max_filesize": 260 * 1024 * 1024,
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
        },
    }
    node_path = shutil.which("node")
    if node_path:
        opts["js_runtimes"] = {"node": {"path": node_path}}
        opts["remote_components"] = {"ejs:github"}
    proxy = get_video_proxy() or ENV_VIDEO_PROXY
    if proxy:
        opts["proxy"] = proxy
    if VIDEO_COOKIES_FILE and Path(VIDEO_COOKIES_FILE).exists():
        opts["cookiefile"] = VIDEO_COOKIES_FILE
    if download:
        opts.update({"format": "bestaudio/best", "outtmpl": output})
    return opts


def _pick_caption(info: dict):
    for source_name, collection in (("subtitle", info.get("subtitles") or {}), ("automatic", info.get("automatic_captions") or {})):
        if not collection:
            continue
        preferred = [k for k in collection if k.lower().startswith("vi")] + [k for k in collection if k.lower().startswith("en")]
        preferred += [k for k in collection if k not in preferred]
        for lang in preferred:
            tracks = collection.get(lang) or []
            for fmt in ("json3", "vtt", "srv3", "ttml"):
                track = next((x for x in tracks if x.get("ext") == fmt and x.get("url")), None)
                if track:
                    return source_name, lang, track
    return None


def _parse_json3(text: str) -> str:
    data = json.loads(text)
    lines = []
    for event in data.get("events", []):
        value = "".join(seg.get("utf8", "") for seg in event.get("segs", []))
        value = re.sub(r"\s+", " ", value).strip()
        if value and (not lines or lines[-1] != value):
            lines.append(value)
    return "\n".join(lines)


def _parse_caption(text: str, ext: str) -> str:
    if ext == "json3":
        return _parse_json3(text)
    cleaned = []
    for line in text.splitlines():
        line = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"\s+", " ", line)
        if not cleaned or cleaned[-1] != line:
            cleaned.append(line)
    return "\n".join(cleaned)


def _download_caption(track: dict) -> str:
    proxy = get_video_proxy() or ENV_VIDEO_PROXY
    with httpx.Client(timeout=40, follow_redirects=True, proxy=proxy) as client:
        response = client.get(track["url"])
        response.raise_for_status()
    return _parse_caption(response.text, track.get("ext", "vtt"))


def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        model_source = whisper_model_path(WHISPER_MODEL)
        _whisper = WhisperModel(model_source, device="cpu", compute_type="int8")
    return _whisper


def _transcribe_audio(url: str, job_id: str) -> str:
    update_video_job(job_id, stage=f"Đang chuyển giọng nói thành văn bản ({WHISPER_MODEL})", progress=48)
    with tempfile.TemporaryDirectory(prefix="aihub-video-") as tmp:
        output = str(Path(tmp) / "audio.%(ext)s")
        last_error = None
        for selector in ("bestaudio[protocol^=http]/bestaudio/best[acodec!=none]/best", "best[acodec!=none]/best"):
            try:
                opts = _ydl_options(True, output)
                opts["format"] = selector
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not files:
            raise RuntimeError("Không tải được luồng âm thanh của video")
        audio = max(files, key=lambda p: p.stat().st_size)
        model = _get_whisper()
        segments, info = model.transcribe(str(audio), beam_size=5, vad_filter=True)
        parts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                parts.append(f"[{segment.start:0.1f}s] {text}")
        return "\n".join(parts).strip()


def extract_video(url: str, job_id: str) -> dict:
    update_video_job(job_id, status="processing", stage="Đang đọc video và metadata", progress=10)
    with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next(iter(info["entries"]))
    duration = float(info.get("duration") or 0)
    if duration and duration > MAX_DURATION:
        raise RuntimeError(f"Video dài quá giới hạn {MAX_DURATION // 60} phút")
    metadata = {
        "title": info.get("title") or "Video chưa có tiêu đề",
        "channel": info.get("channel") or info.get("uploader") or "",
        "thumbnail": info.get("thumbnail") or "",
        "duration": duration,
        "description": (info.get("description") or "")[:8000],
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
    }
    update_video_job(job_id, title=metadata["title"], channel=metadata["channel"], thumbnail=metadata["thumbnail"], duration=duration, stage="Đang tìm phụ đề", progress=28)
    caption = _pick_caption(info)
    transcript = ""
    source = ""
    if caption:
        source_name, lang, track = caption
        try:
            transcript = _download_caption(track)
            source = f"{source_name}:{lang}"
        except Exception:
            transcript = ""
    if not transcript:
        try:
            transcript = _transcribe_audio(url, job_id)
        except Exception:
            transcript = ""
        if transcript:
            source = f"whisper:{WHISPER_MODEL}"
    if not transcript:
        fallback = "\n\n".join(x for x in [metadata["title"], metadata["description"]] if x).strip()
        if len(fallback) < 20:
            raise RuntimeError("Không tìm thấy phụ đề, lời nói hoặc caption đủ nội dung để phân tích")
        transcript = fallback
        source = "metadata:caption"
    update_video_job(job_id, transcript=transcript, transcript_source=source, stage="Đã tạo transcript/nội dung", progress=52)
    return {"metadata": metadata, "transcript": transcript, "source": source}


def extract_visual_frames(url: str, job_id: str, duration: float) -> list[dict]:
    update_video_job(job_id, vision_status="processing", stage="Đang tải luồng hình ảnh", progress=58)
    with tempfile.TemporaryDirectory(prefix="aihub-vision-") as tmp:
        output = str(Path(tmp) / "video.%(ext)s")
        last_error = None
        for selector in ("best[height<=720][vcodec!=none]/best[height<=1080][vcodec!=none]/best[vcodec!=none]/best", "best[vcodec!=none]/best"):
            try:
                opts = _ydl_options(True, output)
                opts["format"] = selector
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not files:
            raise RuntimeError("Không tải được luồng hình ảnh của video")
        media = max(files, key=lambda p: p.stat().st_size)
        update_video_job(job_id, stage="Đang trích xuất keyframe đại diện", progress=64)
        keyframes = extract_keyframes(str(media), job_id, duration)
        update_video_job(job_id, keyframes_json=keyframes_json(keyframes), stage=f"Đã lấy {len(keyframes)} keyframe", progress=69)
        return keyframes


async def _summarize_long(provider: str, credentials: dict, model: str, transcript: str) -> str:
    chunks = [transcript[i:i + 18000] for i in range(0, len(transcript), 18000)]
    summaries = []
    for index, chunk in enumerate(chunks, 1):
        prompt = [
            {"role": "system", "content": "Bạn tóm tắt transcript video chính xác, không bịa. Giữ các luận điểm, dữ kiện, ví dụ, mốc thời gian và câu nói quan trọng."},
            {"role": "user", "content": f"Phần {index}/{len(chunks)} của transcript:\n\n{chunk}\n\nHãy tạo bản tóm tắt chi tiết phần này."},
        ]
        summaries.append(await run_chat(provider, credentials["api_key"], credentials.get("base_url"), model, prompt))
    return "\n\n".join(f"### Tóm tắt phần {i + 1}\n{x}" for i, x in enumerate(summaries))


async def build_report(job: dict, extracted: dict) -> str:
    credentials = get_provider(job["provider"])
    if not credentials:
        raise RuntimeError("API key của nhà cung cấp đã bị gỡ")
    transcript = extracted["transcript"]
    if len(transcript) > 30000:
        update_video_job(job["id"], stage="AI đang tóm tắt transcript dài", progress=82)
        transcript_for_ai = await _summarize_long(job["provider"], credentials, job["model"], transcript)
        source_note = "Transcript dài đã được chia phần và tóm tắt trước khi tổng hợp."
    else:
        transcript_for_ai = transcript
        source_note = "AI phân tích trực tiếp transcript đầy đủ."
    meta = extracted["metadata"]
    visual = extracted.get("visual_summary") or ""
    vision_status = extracted.get("vision_status") or "not_available"
    vision_model = extracted.get("vision_model") or "Không có"
    if visual:
        vision_note = "Các ghi chú hình ảnh đến từ keyframe được lấy theo timeline; đây không phải quan sát mọi frame liên tục của video."
    else:
        vision_note = "Không có dữ liệu Vision đáng tin cậy; không được suy đoán nội dung hình ảnh ngoài metadata/transcript."
    system = """Bạn là chuyên gia phân tích video đa phương thức. Hãy phân biệt rõ dữ liệu nghe/đọc từ transcript với dữ liệu nhìn từ keyframe. Chỉ kết luận từ dữ liệu được cung cấp, không bịa các cảnh không có trong ghi chú Vision. Viết báo cáo bằng tiếng Việt, rõ ràng, có cấu trúc Markdown và nêu điểm cần kiểm chứng khi phù hợp."""
    user = f"""Hãy phân tích video sau.\n\nNỀN TẢNG: {job['platform']}\nTIÊU ĐỀ: {meta['title']}\nKÊNH/TÁC GIẢ: {meta['channel']}\nTHỜI LƯỢNG: {meta['duration']:.0f} giây\nMÔ TẢ: {meta['description']}\nNGUỒN TRANSCRIPT: {extracted['source']}\nGHI CHÚ TRANSCRIPT: {source_note}\nTRẠNG THÁI VISION: {vision_status}\nMODEL VISION: {vision_model}\nGHI CHÚ VISION: {vision_note}\n\nTRANSCRIPT/NỘI DUNG NGÔN NGỮ:\n{transcript_for_ai}\n\nGHI CHÚ HÌNH ẢNH TỪ KEYFRAME:\n{visual or '[Không có dữ liệu Vision]'}\n\nBáo cáo bắt buộc gồm:\n1. Tóm tắt điều hành\n2. Nội dung và diễn biến chính, kết hợp hình + tiếng khi có dữ liệu\n3. Các luận điểm/thông điệp quan trọng\n4. Phân tích hình ảnh: nhân vật, bối cảnh, vật thể, chữ trên màn hình và thay đổi cảnh (chỉ khi Vision có dữ liệu)\n5. Cấu trúc kể chuyện, hook hình/tiếng và cách giữ người xem\n6. Dữ kiện, tuyên bố hoặc chi tiết cần kiểm chứng\n7. Giọng điệu, đối tượng khán giả và mục tiêu nội dung\n8. Insight có thể ứng dụng hoặc tái sử dụng\n9. Kết luận tổng hợp và giới hạn của dữ liệu phân tích\n"""
    update_video_job(job["id"], stage=f"{job['provider']} đang tổng hợp báo cáo đa phương thức", progress=90)
    return await run_chat(job["provider"], credentials["api_key"], credentials.get("base_url"), job["model"], [
        {"role": "system", "content": system}, {"role": "user", "content": user}
    ])


async def process_video_job(job_id: str) -> None:
    job = get_video_job(job_id)
    if not job:
        return
    try:
        extracted = await asyncio.to_thread(extract_video, job["url"], job_id)
        visual_summary = ""
        vision_status = "failed"
        vision_model = None
        vision_note = ""
        try:
            keyframes = await asyncio.to_thread(extract_visual_frames, job["url"], job_id, extracted["metadata"]["duration"])
            credentials = get_provider(job["provider"])
            if not credentials:
                raise RuntimeError("API key của nhà cung cấp đã bị gỡ trước bước Vision")
            update_video_job(job_id, stage=f"{job['provider']} đang xem {len(keyframes)} keyframe", progress=74)
            visual_summary, used, vision_note = await analyze_keyframes(job, credentials, keyframes)
            if visual_summary:
                vision_status = "completed"
                vision_model = used
            else:
                vision_status = used if used in {"unsupported", "failed"} else "failed"
            update_video_job(job_id, vision_status=vision_status, vision_model=vision_model, vision_note=vision_note, visual_summary=visual_summary, stage="Đã hoàn tất bước Vision" if visual_summary else "Vision không khả dụng - tiếp tục phân tích text", progress=79)
        except Exception as vision_exc:
            vision_note = str(vision_exc)[:1500]
            update_video_job(job_id, vision_status="failed", vision_note=vision_note, stage="Không lấy được Vision - tiếp tục phân tích text", progress=79)
        extracted.update({"visual_summary": visual_summary, "vision_status": vision_status, "vision_model": vision_model, "vision_note": vision_note})
        report = await build_report(job, extracted)
        update_video_job(job_id, report=report, status="completed", stage="Hoàn tất phân tích đa phương thức", progress=100, error=None)
    except Exception as exc:
        message = str(exc)
        if "Sign in to confirm you’re not a bot" in message or "Sign in to confirm you're not a bot" in message:
            message = "YouTube đang chặn IP cloud của máy chủ. Hãy mở mục Proxy SOCKS5 trong Phân tích Video, nhập proxy và bấm Kiểm tra proxy + YouTube trước khi thử lại."
        elif "Unexpected response from webpage request" in message and job.get("platform") == "tiktok":
            message = "TikTok từ chối request từ máy chủ hiện tại. Có thể cấu hình VIDEO_PROXY để sử dụng IP phù hợp rồi thử lại."
        update_video_job(job_id, status="failed", stage="Phân tích thất bại", error=message[:1600])
