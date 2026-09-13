"""Automatic local media cleanup for Bot V2.

The downloader intentionally uses local disk as a temporary staging area.
This module keeps that area bounded and removes artifacts that can no longer
be useful to the running queue.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# Start cleanup before the filesystem becomes critically full.
CLEANUP_TRIGGER_PERCENT = 80.0
CLEANUP_TARGET_PERCENT = 70.0
CRITICAL_TRIGGER_PERCENT = 90.0

# Temporary artifacts older than this are safe to remove when they are not
# associated with an active task.
STALE_TEMP_SECONDS = 30 * 60


def disk_usage_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return (usage.used * 100.0 / usage.total) if usage.total else 0.0


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return (p for p in root.rglob("*") if p.is_file())


def _remove_file(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        return size
    except OSError as exc:
        logger.warning("cleanup could not remove %s: %s", path, exc)
        return 0


def _remove_dir(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        logger.warning("cleanup could not remove directory %s: %s", path, exc)
    return total


def cleanup_download_artifacts(
    download_dir: str | Path,
    *,
    active_paths: set[str | Path] | None = None,
    referenced_paths: set[str | Path] | None = None,
    force: bool = False,
) -> dict[str, int | float]:
    """Remove stale/failed/unreferenced artifacts and enforce disk limits.

    Active and explicitly referenced paths are never deleted. When disk usage
    reaches the trigger, the oldest remaining files are deleted until usage
    reaches the target. Split directories and partial-download files are
    always considered temporary when they are not protected.
    """
    root = Path(download_dir)
    root.mkdir(parents=True, exist_ok=True)

    protected: set[Path] = set()
    for values in (active_paths or set(), referenced_paths or set()):
        for value in values:
            p = Path(value)
            protected.add(p.resolve())

    now = time.time()
    removed_bytes = 0
    removed_files = 0
    removed_dirs = 0

    # First remove obvious temporary artifacts that are old and not protected.
    for path in list(root.iterdir() if root.exists() else []):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in protected:
            continue

        name = path.name
        is_temp_dir = path.is_dir() and (name.startswith(".") and (name.endswith("_parts") or "parts" in name))
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
            resolved = path.resolve()
            if resolved in protected:
                continue
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path, size))

    candidates.sort(key=lambda item: item[0])
    for _mtime, path, size in candidates:
        if disk_usage_percent(root) <= CLEANUP_TARGET_PERCENT:
            break
        removed = _remove_file(path)
        if removed:
            removed_bytes += removed
            removed_files += 1

    # Empty directories left by deleted files are harmless, but removing them
    # prevents a buildup of abandoned split/download directories.
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass

    usage = disk_usage_percent(root)
    logger.info(
        "disk cleanup: usage=%.1f%% removed_files=%s removed_dirs=%s freed=%.1fMiB",
        usage,
        removed_files,
        removed_dirs,
        removed_bytes / 1024 / 1024,
    )
    return {
        "usage_percent": usage,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "removed_bytes": removed_bytes,
    }


async def cleanup_download_artifacts_async(download_dir: str | Path, **kwargs):
    return await asyncio.to_thread(cleanup_download_artifacts, download_dir, **kwargs)
