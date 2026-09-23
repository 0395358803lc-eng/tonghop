from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from .config import MEDIA_DIR, TEMP_DIR

GIB = 1024 ** 3
DEFAULT_TEMP_QUOTA_GB = 10.0
WARN_DISK_GB = 15.0
BLOCK_DISK_GB = 5.0
WARN_RAM_GB = 4.0
BLOCK_RAM_GB = 1.5


def _bytes_under(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _gpu_status() -> list[dict]:
    command = shutil.which("nvidia-smi")
    if not command:
        return []
    try:
        proc = subprocess.run(
            [
                command,
                "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if proc.returncode != 0:
            return []
        items = []
        for line in (proc.stdout or "").splitlines():
            parts = [item.strip() for item in line.split(",")]
            if len(parts) != 4:
                continue
            items.append(
                {
                    "name": parts[0],
                    "memory_total_mb": float(parts[1]),
                    "memory_free_mb": float(parts[2]),
                    "utilization_percent": float(parts[3]),
                }
            )
        return items
    except Exception:
        return []
def resource_status() -> dict:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(MEDIA_DIR)
    cpu_percent = psutil.cpu_percent(interval=0.05)
    free_disk_gb = round(disk.free / GIB, 2)
    available_ram_gb = round(memory.available / GIB, 2)
    warnings: list[str] = []
    blockers: list[str] = []

    if free_disk_gb < BLOCK_DISK_GB:
        blockers.append(f"DISK_CRITICAL:{free_disk_gb}GB")
    elif free_disk_gb < WARN_DISK_GB:
        warnings.append(f"DISK_LOW:{free_disk_gb}GB")

    if available_ram_gb < BLOCK_RAM_GB:
        blockers.append(f"RAM_CRITICAL:{available_ram_gb}GB")
    elif available_ram_gb < WARN_RAM_GB:
        warnings.append(f"RAM_LOW:{available_ram_gb}GB")

    if cpu_percent >= 95:
        warnings.append(f"CPU_HIGH:{round(cpu_percent, 1)}%")

    return {
        "ok": True,
        "can_render": not blockers,
        "warnings": warnings,
        "blockers": blockers,
        "cpu": {
            "logical_cores": psutil.cpu_count(logical=True) or 0,
            "physical_cores": psutil.cpu_count(logical=False) or 0,
            "usage_percent": round(cpu_percent, 1),
        },
        "memory": {
            "total_gb": round(memory.total / GIB, 2),
            "available_gb": available_ram_gb,
            "used_percent": round(float(memory.percent), 1),
        },
        "storage": {
            "media_dir": str(MEDIA_DIR),
            "total_gb": round(disk.total / GIB, 2),
            "free_gb": free_disk_gb,
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0,
        },
        "temp": {
            "dir": str(TEMP_DIR),
            "size_gb": round(_bytes_under(TEMP_DIR) / GIB, 3),
            "quota_gb": float(os.getenv("TH_MEDIA_TEMP_QUOTA_GB", str(DEFAULT_TEMP_QUOTA_GB))),
        },
        "gpus": _gpu_status(),
    }
def cleanup_temp(quota_gb: float | None = None, min_age_seconds: int = 300) -> dict:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    quota = float(quota_gb if quota_gb is not None else os.getenv("TH_MEDIA_TEMP_QUOTA_GB", str(DEFAULT_TEMP_QUOTA_GB)))
    quota = max(0.5, min(quota, 500.0))
    limit = int(quota * GIB)
    target = int(limit * 0.80)
    files: list[tuple[float, Path, int]] = []
    total = 0
    now = time.time()

    for path in TEMP_DIR.rglob("*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
            total += stat.st_size
            if now - stat.st_mtime >= min_age_seconds:
                files.append((stat.st_mtime, path, stat.st_size))
        except OSError:
            continue

    before = total
    deleted = 0
    deleted_bytes = 0
    if total > limit:
        for _, path, size in sorted(files, key=lambda item: item[0]):
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
                deleted += 1
                deleted_bytes += size
            except OSError:
                continue

    for folder in sorted((p for p in TEMP_DIR.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "quota_gb": quota,
        "before_gb": round(before / GIB, 3),
        "after_gb": round(total / GIB, 3),
        "deleted_files": deleted,
        "deleted_gb": round(deleted_bytes / GIB, 3),
        "temp_dir": str(TEMP_DIR),
    }


def assert_render_resources() -> dict:
    cleanup = cleanup_temp()
    status = resource_status()
    status["temp_cleanup"] = cleanup
    if not status["can_render"]:
        raise ValueError(
            "DESKTOP_RESOURCE_BLOCKED: "
            + "; ".join(status.get("blockers") or ["Tài nguyên máy không đủ để bắt đầu render."])
        )
    return status
