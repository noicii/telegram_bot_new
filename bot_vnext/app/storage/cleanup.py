"""Automatic local media cleanup for Bot V2.

The downloader intentionally uses local disk as a temporary staging area.
This module keeps that area bounded and removes artifacts that can no longer
be useful to the running queue.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

CLEANUP_TRIGGER_PERCENT = 80.0
CLEANUP_TARGET_PERCENT = 70.0
CRITICAL_TRIGGER_PERCENT = 90.0
STALE_TEMP_SECONDS = 30 * 60


def disk_usage_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return (usage.used * 100.0 / usage.total) if usage.total else 0.0


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return (p for p in root.rglob("*") if p.is_file())


def remove_artifact(path: str | Path) -> int:
    """Delete a media artifact and its generated split directory."""
    target = Path(path)
    freed = 0
    try:
        if target.is_file() or target.is_symlink():
            try:
                freed += target.stat().st_size
            except OSError:
                pass
            target.unlink(missing_ok=True)
        parts_dir = target.parent / f".{target.stem}_parts"
        if parts_dir.is_dir():
            for item in parts_dir.rglob("*"):
                if item.is_file():
                    try:
                        freed += item.stat().st_size
                    except OSError:
                        pass
            shutil.rmtree(parts_dir, ignore_errors=True)
    except OSError as exc:
        logger.warning("cleanup could not remove %s: %s", target, exc)
    return freed


def cleanup_download_artifacts(
    download_dir: str | Path,
    *,
    active_paths: set[str | Path] | None = None,
    force: bool = False,
) -> dict[str, int | float]:
    """Remove stale temporary artifacts and enforce disk usage limits.

    Active paths are never deleted. At/above the trigger threshold, the oldest
    non-active files are removed until usage reaches the target threshold.
    """
    root = Path(download_dir)
    root.mkdir(parents=True, exist_ok=True)

    protected: set[Path] = set()
    for value in active_paths or set():
        try:
            protected.add(Path(value).resolve())
        except OSError:
            protected.add(Path(value))

    now = time.time()
    removed_bytes = 0
    removed_files = 0
    removed_dirs = 0

    for path in list(root.iterdir() if root.exists() else []):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in protected:
            continue

        name = path.name
        is_temp_dir = path.is_dir() and name.startswith(".") and name.endswith("_parts")
        is_temp_file = path.is_file() and (
            name.endswith(".part") or name.endswith(".tmp") or name.endswith(".ytdl") or name.startswith(".")
        )
        if is_temp_dir or is_temp_file:
            try:
                age = now - path.stat().st_mtime
            except OSError:
                age = 0
            if force or age >= STALE_TEMP_SECONDS:
                if path.is_dir():
                    removed_bytes += _remove_dir(path)
                    removed_dirs += 1
                else:
                    removed_bytes += _remove_file(path)
                    removed_files += 1

    usage = disk_usage_percent(root)
    trigger = CRITICAL_TRIGGER_PERCENT if usage >= CRITICAL_TRIGGER_PERCENT else CLEANUP_TRIGGER_PERCENT
    if not force and usage < trigger:
        return {
            "usage_percent": usage,
            "removed_files": removed_files,
            "removed_dirs": removed_dirs,
            "removed_bytes": removed_bytes,
        }

    candidates: list[tuple[float, Path, int]] = []
    for path in _iter_files(root):
        try:
            if path.resolve() in protected:
                continue
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path, size))

    candidates.sort(key=lambda item: item[0])
    for _mtime, path, _size in candidates:
        if disk_usage_percent(root) <= CLEANUP_TARGET_PERCENT:
            break
        removed = _remove_file(path)
        if removed:
            removed_bytes += removed
            removed_files += 1

    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass

    usage = disk_usage_percent(root)
    logger.info(
        "disk cleanup: usage=%.1f%% removed_files=%s removed_dirs=%s freed=%.1fMiB",
        usage, removed_files, removed_dirs, removed_bytes / 1024 / 1024,
    )
    return {
        "usage_percent": usage,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "removed_bytes": removed_bytes,
    }


async def cleanup_download_artifacts_async(download_dir: str | Path, **kwargs):
    return await asyncio.to_thread(cleanup_download_artifacts, download_dir, **kwargs)


async def remove_artifact_async(path: str | Path) -> int:
    return await asyncio.to_thread(remove_artifact, path)
