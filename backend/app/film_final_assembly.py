from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from runtime_dependencies import resolve_executable

from .film_acceptance_snapshot import refresh_project_staleness
from .film_boundary_service import ensure_junctions, refresh_junction_staleness
from .film_boundary_store import get_pair_junction
from .film_final_store import create_final_render, get_final_render, list_final_renders, update_final_render
from .film_media_store import get_media, get_selected_media, output_key_for, project_media_root, public_media, register_completed_media, validate_media_path
from .film_scene_state_store import list_scene_states
from .film_store import get_film_project

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
TARGET_FPS = 24
TARGET_AUDIO_RATE = 48000
TARGET_AUDIO_CH = 2


def _ffmpeg(name: str = "ffmpeg") -> str:
    return resolve_executable(name)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _ordered_scenes(project: dict) -> list[dict]:
    return sorted(project.get("scenes") or [], key=lambda x: int(x.get("scene_index") or 0))


def _selected_video(project_id: str, scene_id: str):
    key = output_key_for(role="scene_video", scene_id=scene_id)
    selected = get_selected_media(project_id, key)
    if selected and selected.get("role") == "repair_candidate":
        return None
    return selected


def evaluate_assembly_gate(project_id: str) -> dict:
    project = get_film_project(project_id)
    if not project:
        raise ValueError("Không tìm thấy dự án phim.")
    scenes = _ordered_scenes(project)
    errors = []
    items = []
    if not scenes:
        errors.append({"code": "NO_SCENES", "detail": "Dự án chưa có scene."})
    indexes = [int(scene.get("scene_index") or 0) for scene in scenes]
    if indexes != sorted(indexes):
        errors.append({"code": "SCENE_ORDER_INVALID", "detail": "Thứ tự scene_index không hợp lệ."})
    ensure_junctions(project_id)
    refresh_junction_staleness(project_id)
    refresh_project_staleness(project_id)
    states = {item["scene_id"]: item for item in list_scene_states(project_id)}
    for scene in scenes:
        sid = scene["id"]
        state = states.get(sid) or {}
        status = state.get("status") or "LOCKED"
        if status == "STALE":
            errors.append({"code": "SCENE_STALE", "scene_id": sid})
        elif status == "BLOCKED":
            errors.append({"code": "SCENE_BLOCKED", "scene_id": sid})
        elif status != "APPROVED":
            errors.append({"code": "SCENE_NOT_APPROVED", "scene_id": sid, "status": status})
        media = _selected_video(project_id, sid)
        if not media or not media.get("is_selected"):
            errors.append({"code": "SELECTED_MEDIA_REQUIRED", "scene_id": sid})
            continue
        if media.get("status") != "completed" or media.get("qc_status") != "passed":
            errors.append({"code": "SELECTED_MEDIA_INVALID", "scene_id": sid})
            continue
        try:
            path = validate_media_path(media.get("file_path"))
        except ValueError:
            errors.append({"code": "SELECTED_MEDIA_FILE_MISSING", "scene_id": sid})
            continue
        items.append({
            "scene_id": sid,
            "scene_index": int(scene.get("scene_index") or 0),
            "media_id": media["id"],
            "version": int(media.get("version") or 1),
            "file_path_internal": str(path),
        })
    for prev, nxt in zip(scenes, scenes[1:]):
        row = get_pair_junction(project_id, prev["id"], nxt["id"])
        st = (row or {}).get("status")
        if st == "STALE":
            errors.append({"code": "JUNCTION_STALE", "previous_scene_id": prev["id"], "next_scene_id": nxt["id"]})
        elif st != "PASS":
            errors.append({"code": "JUNCTION_NOT_PASS", "previous_scene_id": prev["id"], "next_scene_id": nxt["id"], "status": st or "MISSING"})
    passed = not errors
    return {
        "passed": passed,
        "code": None if passed else "FINAL_ASSEMBLY_BLOCKED",
        "errors": errors,
        "items": items,
        "scene_count": len(scenes),
        "approved_count": sum(1 for scene in scenes if (states.get(scene["id"]) or {}).get("status") == "APPROVED"),
    }


def build_manifest(project_id: str, items: list[dict], version: int) -> dict:
    payload = {
        "project_id": project_id,
        "scene_count": len(items),
        "version": version,
        "items": [
            {
                "scene_id": item["scene_id"],
                "scene_index": item["scene_index"],
                "media_id": item["media_id"],
                "version": item["version"],
                "file_path_internal": item["file_path_internal"],
            }
            for item in items
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def public_manifest(manifest: dict | None) -> dict:
    def scrub(value):
        if isinstance(value, dict):
            return {key: scrub(val) for key, val in value.items() if key != "file_path_internal"}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value
    return scrub(dict(manifest or {}))


def _probe(path: Path) -> dict:
    proc = _run([
        _ffmpeg("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ])
    if proc.returncode != 0:
        raise ValueError(f"FFPROBE_FAILED: {proc.stderr[-400:]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), {})
    return {
        "video_codec": video.get("codec_name"),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate"),
        "pix_fmt": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name"),
        "sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "channels": int(audio.get("channels") or 0) if audio else 0,
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "has_audio": bool(audio),
    }


def _needs_normalize(info: dict) -> bool:
    if info.get("video_codec") != "h264":
        return True
    if info.get("width") != TARGET_WIDTH or info.get("height") != TARGET_HEIGHT:
        return True
    if info.get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
        return True
    if not info.get("has_audio"):
        return True
    if info.get("audio_codec") != "aac":
        return True
    if info.get("sample_rate") != TARGET_AUDIO_RATE or info.get("channels") != TARGET_AUDIO_CH:
        return True
    fps = str(info.get("fps") or "")
    if fps not in {"24/1", "24", f"{TARGET_FPS}/1"}:
        return True
    return False


def _normalize(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps={TARGET_FPS},format=yuv420p"
    )
    args = [
        _ffmpeg(), "-y", "-i", str(src),
        "-f", "lavfi", "-t", "0.1", "-i", f"anullsrc=r={TARGET_AUDIO_RATE}:cl=stereo",
        "-filter_complex", f"[0:v]{vf}[v];[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", str(TARGET_AUDIO_RATE), "-ac", str(TARGET_AUDIO_CH),
        "-movflags", "+faststart", str(dest),
    ]
    info = _probe(src)
    if not info.get("has_audio"):
        args = [
            _ffmpeg(), "-y", "-i", str(src),
            "-f", "lavfi", "-i", f"anullsrc=r={TARGET_AUDIO_RATE}:cl=stereo",
            "-vf", vf, "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", str(TARGET_AUDIO_RATE), "-ac", str(TARGET_AUDIO_CH),
            "-movflags", "+faststart", str(dest),
        ]
    else:
        args = [
            _ffmpeg(), "-y", "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", str(TARGET_AUDIO_RATE), "-ac", str(TARGET_AUDIO_CH),
            "-movflags", "+faststart", str(dest),
        ]
    proc = _run(args)
    if proc.returncode != 0 or not dest.is_file():
        raise ValueError(f"FFMPEG_NORMALIZE_FAILED: {proc.stderr[-500:]}")


def _concat(files: list[Path], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.parent / f"{dest.stem}_concat.txt"
    lines = []
    for path in files:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run([
        _ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart", str(dest),
    ])
    if proc.returncode != 0 or not dest.is_file():
        proc = _run([
            _ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(dest),
        ])
    if proc.returncode != 0 or not dest.is_file():
        raise ValueError(f"FFMPEG_CONCAT_FAILED: {proc.stderr[-500:]}")


def assemble(project_id: str) -> dict:
    gate = evaluate_assembly_gate(project_id)
    if not gate["passed"]:
        safe_gate = {
            "passed": False,
            "code": gate.get("code"),
            "errors": gate.get("errors") or [],
            "scene_count": gate.get("scene_count"),
            "approved_count": gate.get("approved_count"),
        }
        row = create_final_render(
            project_id,
            status="ASSEMBLY_FAILED",
            error="FINAL_ASSEMBLY_BLOCKED",
            manifest={"gate": safe_gate, "items": []},
        )
        raise ValueError("FINAL_ASSEMBLY_BLOCKED")
    items = sorted(gate["items"], key=lambda x: int(x["scene_index"]))
    row = create_final_render(project_id, status="ASSEMBLING", manifest={"gate": {"passed": True}})
    version = int(row["version"])
    try:
        work = project_media_root(project_id) / "final" / f"v{version}"
        work.mkdir(parents=True, exist_ok=True)
        normalized = []
        for item in items:
            src = Path(item["file_path_internal"])
            dest = work / f"{int(item['scene_index']):04d}_{item['scene_id']}.mp4"
            info = _probe(src)
            if _needs_normalize(info):
                _normalize(src, dest)
            else:
                shutil.copy2(src, dest)
            normalized.append(dest)
        output = project_media_root(project_id) / "final" / f"final_v{version}.mp4"
        _concat(normalized, output)
        info = _probe(output)
        manifest = build_manifest(project_id, items, version)
        media = register_completed_media(
            project_id=project_id,
            media_type="video",
            role="final_video",
            file_path=output,
            provider="ffmpeg",
            model="concat-normalize",
            provider_job_id=f"final-{project_id}-v{version}",
            mime_type="video/mp4",
            width=info.get("width") or TARGET_WIDTH,
            height=info.get("height") or TARGET_HEIGHT,
            duration_seconds=info.get("duration"),
            qc_status="pending",
            metadata={
                "scene_count": len(items),
                "source_media_ids": [item["media_id"] for item in items],
                "source_versions": [item["version"] for item in items],
                "assembly_manifest_hash": manifest["manifest_hash"],
                "video_codec": info.get("video_codec"),
                "audio_codec": info.get("audio_codec"),
                "fps": info.get("fps"),
                "download_name": f"final_v{version}.mp4",
            },
            select_if_passed=False,
        )
        row = update_final_render(
            project_id, row["id"],
            status="QC_PENDING",
            media_id=media["id"],
            manifest=manifest,
            manifest_hash=manifest["manifest_hash"],
            error=None,
        )
    except Exception as exc:
        update_final_render(project_id, row["id"], status="ASSEMBLY_FAILED", error=str(exc)[:800])
        raise
    return final_status(project_id, row["id"])


def final_status(project_id: str, render_id: str | None = None) -> dict:
    if not get_film_project(project_id):
        raise ValueError("Không tìm thấy dự án phim.")
    gate = evaluate_assembly_gate(project_id)
    row = get_final_render(project_id, render_id)
    media = get_media(row["media_id"]) if row and row.get("media_id") else None
    return {
        "project_id": project_id,
        "gate": {"passed": gate["passed"], "code": gate.get("code"), "errors": gate.get("errors") or [], "scene_count": gate.get("scene_count"), "approved_count": gate.get("approved_count")},
        "current": _public_render(row, media),
        "history": [_public_render(item) for item in list_final_renders(project_id)],
    }


def _public_render(row: dict | None, media: dict | None = None) -> dict | None:
    if not row:
        return None
    data = dict(row)
    data["manifest"] = public_manifest(row.get("manifest") or {})
    data["qc"] = row.get("qc") or {}
    if media is None and row.get("media_id"):
        media = get_media(row["media_id"])
    data["media"] = public_media(media) if media else None
    return data


def assemble_project(project_id: str) -> dict:
    from .film_event_store import emit_event
    current = get_final_render(project_id)
    if current and current.get("status") in {"ASSEMBLING", "QC_PENDING", "QC_RUNNING", "APPROVED"}:
        return final_status(project_id, current["id"])
    emit_event(project_id, "FINAL_ASSEMBLY_STARTED")
    result = assemble(project_id)
    emit_event(project_id, "FINAL_ASSEMBLY_COMPLETED", payload={"render_id": ((result.get("current") or {}).get("id"))})
    return result
