import base64
import json
import os
import shutil
from pathlib import Path

import av
import httpx
from PIL import Image

from .config import DATA_DIR, MEDIA_DIR
from .providers.vision import run_vision
from .video_store import update_video_job

FRAMES_ROOT = MEDIA_DIR / "video_frames"
FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
MAX_FRAMES = max(3, min(int(os.getenv("VIDEO_VISION_MAX_FRAMES", "8")), 12))
MAX_WIDTH = max(320, min(int(os.getenv("VIDEO_VISION_MAX_WIDTH", "640")), 1280))
JPEG_QUALITY = max(45, min(int(os.getenv("VIDEO_VISION_JPEG_QUALITY", "72")), 90))

FREE_VISION_FALLBACKS = {
    "xkiro": [
        "deepseek/deepseek-v4-flash-vision-exp",
        "qwen/qwen3-vl-plus:free",
        "qwen/qwen3-omni-flash:free",
    ]
}


def delete_video_artifacts(job_id: str) -> None:
    shutil.rmtree(FRAMES_ROOT / job_id, ignore_errors=True)


def get_frame_path(job_id: str, filename: str) -> Path | None:
    safe_name = Path(filename).name
    if safe_name != filename:
        return None
    path = FRAMES_ROOT / job_id / safe_name
    return path if path.is_file() else None


def _duration_seconds(container: av.container.InputContainer, stream, hint: float) -> float:
    if hint and hint > 0:
        return float(hint)
    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration:
        return float(container.duration / av.time_base)
    return 0.0


def _frame_count(duration: float) -> int:
    if duration <= 25:
        return min(MAX_FRAMES, 5)
    if duration <= 120:
        return min(MAX_FRAMES, 6)
    if duration <= 600:
        return min(MAX_FRAMES, 8)
    return MAX_FRAMES


def extract_keyframes(video_path: str, job_id: str, duration_hint: float = 0) -> list[dict]:
    delete_video_artifacts(job_id)
    target_dir = FRAMES_ROOT / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    container = av.open(video_path)
    try:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise RuntimeError("Luá»“ng media khÃ´ng chá»©a hÃ¬nh áº£nh video")
        duration = _duration_seconds(container, stream, duration_hint)
        if duration <= 0:
            raise RuntimeError("KhÃ´ng xÃ¡c Ä‘á»‹nh Ä‘Æ°á»£c thá»i lÆ°á»£ng video Ä‘á»ƒ láº¥y keyframe")
        count = _frame_count(duration)
        timestamps = [duration * (i + 0.5) / count for i in range(count)]
        frames = []
        previous_time = -99.0
        for index, target in enumerate(timestamps):
            if stream.time_base is None:
                break
            offset = max(0, int(target / float(stream.time_base)))
            container.seek(offset, stream=stream, any_frame=False, backward=True)
            chosen = None
            for frame in container.decode(stream):
                chosen = frame
                current = float(frame.time or 0)
                if current >= target - 0.12:
                    break
            if chosen is None:
                continue
            actual = float(chosen.time if chosen.time is not None else target)
            if abs(actual - previous_time) < 0.25:
                continue
            previous_time = actual
            image = chosen.to_image().convert("RGB")
            if image.width > MAX_WIDTH:
                ratio = MAX_WIDTH / image.width
                image = image.resize((MAX_WIDTH, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
            filename = f"frame_{len(frames) + 1:02d}.jpg"
            path = target_dir / filename
            image.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
            frames.append({
                "index": len(frames) + 1,
                "timestamp": round(actual, 2),
                "filename": filename,
                "url": f"/api/video/jobs/{job_id}/frames/{filename}",
            })
        if not frames:
            raise RuntimeError("KhÃ´ng trÃ­ch xuáº¥t Ä‘Æ°á»£c keyframe tá»« video")
        return frames
    finally:
        container.close()


def load_frame_payloads(job_id: str, keyframes: list[dict]) -> list[dict]:
    payloads = []
    for frame in keyframes:
        path = get_frame_path(job_id, frame["filename"])
        if not path:
            continue
        payloads.append({
            "timestamp": float(frame["timestamp"]),
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    return payloads


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        text = exc.response.text.replace("\n", " ")[:700]
        return f"HTTP {exc.response.status_code}: {text}"
    return str(exc)[:700]


async def analyze_keyframes(job: dict, credentials: dict, keyframes: list[dict]) -> tuple[str, str, str]:
    images = load_frame_payloads(job["id"], keyframes)
    if not images:
        return "", "failed", "KhÃ´ng Ä‘á»c Ä‘Æ°á»£c dá»¯ liá»‡u keyframe Ä‘Ã£ trÃ­ch xuáº¥t"
    prompt = """Báº¡n Ä‘ang xem cÃ¡c keyframe Ä‘áº¡i diá»‡n Ä‘Æ°á»£c láº¥y theo timeline cá»§a má»™t video. HÃ£y chá»‰ mÃ´ táº£ nhá»¯ng gÃ¬ thá»±c sá»± quan sÃ¡t tháº¥y trong áº£nh, khÃ´ng suy Ä‘oÃ¡n lá»i thoáº¡i. PhÃ¢n tÃ­ch báº±ng tiáº¿ng Viá»‡t vÃ  táº¡o ghi chÃº thá»‹ giÃ¡c cÃ³ cáº¥u trÃºc gá»“m: (1) diá»…n biáº¿n hÃ¬nh áº£nh theo má»‘c thá»i gian; (2) nhÃ¢n váº­t/ngÆ°á»i xuáº¥t hiá»‡n vÃ  hÃ nh Ä‘á»™ng cÃ³ thá»ƒ quan sÃ¡t; (3) bá»‘i cáº£nh, váº­t thá»ƒ, sáº£n pháº©m; (4) chá»¯/overlay/logo cÃ³ thá»ƒ Ä‘á»c Ä‘Æ°á»£c; (5) phong cÃ¡ch quay, bá»‘ cá»¥c, mÃ u sáº¯c, chuyá»ƒn biáº¿n cáº£nh; (6) visual hook vÃ  yáº¿u tá»‘ giá»¯ ngÆ°á»i xem; (7) cÃ¡c chi tiáº¿t khÃ´ng cháº¯c cháº¯n pháº£i ghi rÃµ lÃ  khÃ´ng cháº¯c cháº¯n. Má»—i nháº­n xÃ©t quan trá»ng nÃªn gáº¯n timestamp cá»§a frame tÆ°Æ¡ng á»©ng."""
    candidates = [job["model"]]
    for fallback in FREE_VISION_FALLBACKS.get(job["provider"], []):
        if fallback not in candidates:
            candidates.append(fallback)
    errors = []
    for candidate in candidates:
        try:
            summary = await run_vision(job["provider"], credentials["api_key"], credentials.get("base_url"), candidate, prompt, images)
            if summary.strip():
                note = "Model Ä‘ang chá»n há»— trá»£ Vision." if candidate == job["model"] else f"Model chat khÃ´ng nháº­n áº£nh; tá»± Ä‘á»™ng dÃ¹ng Vision model miá»…n phÃ­ {candidate}."
                return summary.strip(), candidate, note
            errors.append(f"{candidate}: khÃ´ng tráº£ ná»™i dung")
        except Exception as exc:
            errors.append(f"{candidate}: {_error_detail(exc)}")
    return "", "unsupported", " | ".join(errors)[:1500]


def keyframes_json(keyframes: list[dict]) -> str:
    return json.dumps(keyframes, ensure_ascii=False)

