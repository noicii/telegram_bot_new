import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    ADMIN_IDS,
    DOWNLOAD_DIR,
    MAX_SPLIT_SIZE,
    MIN_DISK_FREE_GB,
    SETTINGS,
    STATS,
    WATERMARK_PATH,
    save_stats,
)


def is_authorized(user_id, admin_ids=None):
    if admin_ids is None:
        admin_ids = ADMIN_IDS

    try:
        return int(user_id) in {
            int(admin_id)
            for admin_id in admin_ids
        }
    except (TypeError, ValueError):
        return False


def format_size(size):
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024

    return "0 B"


def format_duration(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "00:00"

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def sanitize_filename(name, default="video"):
    if not name:
        name = default

    name = str(name).strip()

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name,
    )

    name = re.sub(r"\s+", " ", name).strip(" .")

    if not name:
        name = default

    return name[:180]


def clean_title(title, idx=None):
    if not title:
        title = "video"

    title = str(title)

    title = re.sub(
        r"\[[^\]]*\]",
        "",
        title,
    )

    title = re.sub(
        r"\([^)]*\)",
        "",
        title,
    )

    title = sanitize_filename(title)

    if idx is not None:
        try:
            idx = int(idx)

            if idx > 0:
                title = f"{title}_{idx}"

        except (TypeError, ValueError):
            pass

    return title


def clean_media_title(title):
    return clean_title(title)


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or f"Command failed: {result.returncode}"
        )

    return result.stdout.strip()


async def run_command_async(command):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode(
            errors="replace"
        ).strip()

        raise RuntimeError(
            error
            or f"Command failed: {process.returncode}"
        )

    return stdout.decode(
        errors="replace"
    ).strip()


def get_video_metadata(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        output = run_command(command)
        return json.loads(output)
    except Exception:
        return {}


async def async_get_video_metadata(path):
    metadata = get_video_metadata(path)

    duration = 0.0
    width = 0
    height = 0

    try:
        duration = float(
            metadata.get("format", {}).get(
                "duration",
                0,
            )
        )
    except (TypeError, ValueError):
        duration = 0.0

    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                width = int(
                    stream.get("width", 0)
                )
            except (TypeError, ValueError):
                width = 0

            try:
                height = int(
                    stream.get("height", 0)
                )
            except (TypeError, ValueError):
                height = 0

            break

    return duration, width, height


def get_video_duration(path):
    metadata = get_video_metadata(path)

    try:
        return float(
            metadata["format"]["duration"]
        )
    except (KeyError, TypeError, ValueError):
        return 0.0


def get_video_dimensions(path):
    metadata = get_video_metadata(path)

    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                return (
                    int(stream.get("width", 0)),
                    int(stream.get("height", 0)),
                )
            except (TypeError, ValueError):
                pass

    return 0, 0


def check_disk_space_guard(
    required_gb=MIN_DISK_FREE_GB,
):
    usage = shutil.disk_usage(
        str(DOWNLOAD_DIR)
    )

    free_gb = usage.free / (
        1024 ** 3
    )

    if free_gb < required_gb:
        raise RuntimeError(
            f"Low disk space: {free_gb:.2f} GB free"
        )

    return free_gb


def get_main_keyboard():
    def status(value):
        return "✅" if value else "❌"

    keyboard = [
        [
            InlineKeyboardButton(
                f"🗑 Auto Delete {status(SETTINGS.get('auto_delete'))}",
                callback_data="toggle_auto_delete",
            ),
            InlineKeyboardButton(
                f"🗜 Compress {status(SETTINGS.get('compress'))}",
                callback_data="toggle_compress",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🎬 Sample {status(SETTINGS.get('sample_video'))}",
                callback_data="toggle_sample",
            ),
            InlineKeyboardButton(
                f"📝 Subtitles {status(SETTINGS.get('extract_subtitles'))}",
                callback_data="toggle_subtitles",
            ),
        ],
        [
            InlineKeyboardButton(
                f"✏️ Rename {status(SETTINGS.get('custom_rename'))}",
                callback_data="toggle_rename",
            ),
            InlineKeyboardButton(
                f"🎵 Audio: {SETTINGS.get('audio_track_mode', 'all')}",
                callback_data="toggle_audiotrack",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🎚 Preset: {SETTINGS.get('download_preset', 'custom')}",
                callback_data="cycle_preset",
            ),
        ],
        [
            InlineKeyboardButton(
                f"📺 Resolution: {SETTINGS.get('target_resolution', 'original')}",
                callback_data="cycle_resolution",
            ),
            InlineKeyboardButton(
                f"⚡ Speed: {SETTINGS.get('speed_limit', '0')}",
                callback_data="cycle_speed",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Retry Failed",
                callback_data="retry_failed",
            ),
            InlineKeyboardButton(
                "🔄 Retry All",
                callback_data="retry_all",
            ),
        ],
        [
            InlineKeyboardButton(
                (
                    "▶️ Resume Queue"
                    if SETTINGS.get("queue_paused", False)
                    else "⏸ Pause Queue"
                ),
                callback_data="toggle_queue_pause",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Status",
                callback_data="btn_status",
            ),
            InlineKeyboardButton(
                "🗑 Clear Summary",
                callback_data="clear_summary_history",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧹 Clear Cache",
                callback_data="btn_clearcache",
            ),
            InlineKeyboardButton(
                "📋 Pending",
                callback_data="btn_pending",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def trim_video_file(
    input_path,
    trim_info,
):
    if not trim_info:
        return input_path

    try:
        start, end = trim_info
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return input_path

    if end <= start:
        return input_path

    input_path = Path(input_path)

    output_path = input_path.with_name(
        f"{input_path.stem}.trim{input_path.suffix}"
    )

    duration = end - start

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(input_path),
        "-t",
        str(duration),
        "-c",
        "copy",
        str(output_path),
    ]

    await run_command_async(command)

    if output_path.exists():
        cleanup_task_artifacts(input_path)
        return output_path

    return input_path


async def compress_video(
    input_path,
    output_path=None,
):
    input_path = Path(input_path)

    if output_path is None:
        output_path = input_path.with_name(
            f"{input_path.stem}.compressed{input_path.suffix}"
        )
    else:
        output_path = Path(output_path)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output_path),
    ]

    await run_command_async(command)

    if output_path.exists():
        if output_path != input_path:
            cleanup_task_artifacts(input_path)

        return output_path

    return input_path


async def apply_watermark(
    input_path,
    output_path=None,
):
    input_path = Path(input_path)

    if output_path is None:
        output_path = input_path.with_name(
            f"{input_path.stem}.watermark{input_path.suffix}"
        )
    else:
        output_path = Path(output_path)

    if not WATERMARK_PATH.exists():
        return input_path

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-i",
        str(WATERMARK_PATH),
        "-filter_complex",
        "overlay=W-w-20:H-h-20",
        "-c:a",
        "copy",
        str(output_path),
    ]

    await run_command_async(command)

    if output_path.exists():
        if output_path != input_path:
            cleanup_task_artifacts(input_path)

        return output_path

    return input_path


async def filter_audio_tracks(
    input_path,
    mode=None,
):
    input_path = Path(input_path)

    if mode is None:
        mode = SETTINGS.get(
            "audio_track_mode",
            "all",
        )

    if mode == "all":
        return input_path

    if mode not in {
        "first",
        "second",
        "none",
    }:
        return input_path

    output_path = input_path.with_name(
        f"{input_path.stem}.audio{input_path.suffix}"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
    ]

    if mode == "first":
        command.extend([
            "-map",
            "0:a:0",
        ])
    elif mode == "second":
        command.extend([
            "-map",
            "0:a:1",
        ])

    command.extend([
        "-c",
        "copy",
        str(output_path),
    ])

    try:
        await run_command_async(command)

    except Exception:
        return input_path

    if output_path.exists():
        cleanup_task_artifacts(input_path)
        return output_path

    return input_path


async def apply_resolution_downscale(
    input_path,
    resolution="original",
):
    input_path = Path(input_path)

    resolution = str(
        resolution
    ).lower()

    height_map = {
        "360p": 360,
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
    }

    height = height_map.get(
        resolution
    )

    if not height:
        return input_path

    _, current_height = (
        get_video_dimensions(input_path)
    )

    if current_height and current_height <= height:
        return input_path

    output_path = input_path.with_name(
        f"{input_path.stem}.{resolution}{input_path.suffix}"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "copy",
        str(output_path),
    ]

    await run_command_async(command)

    if output_path.exists():
        cleanup_task_artifacts(input_path)
        return output_path

    return input_path

async def extract_subtitles_from_video(video_path):
    video_path = Path(video_path)

    output_path = video_path.with_suffix(".srt")

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:s:0",
        str(output_path),
    ]

    try:
        await run_command_async(command)

        if output_path.exists():
            return output_path

    except Exception:
        return None

    return None
async def generate_sample_clip(
    input_path,
    output_path,
    seconds=30,
):
    duration = get_video_duration(
        input_path
    )

    clip_length = min(
        float(seconds),
        duration,
    )

    if clip_length <= 0:
        return None

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-t",
        str(clip_length),
        "-c",
        "copy",
        str(output_path),
    ]

    try:
        await run_command_async(command)
        return output_path
    except Exception:
        return None


async def generate_screenshots_collage(
    input_path,
    output_path,
    count=4,
):
    duration = get_video_duration(
        input_path
    )

    if duration <= 0:
        return None

    count = max(
        1,
        int(count),
    )

    fps = count / duration

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        (
            f"fps={fps},"
            "scale=320:-1,"
            "tile=2x2"
        ),
        "-frames:v",
        "1",
        str(output_path),
    ]

    try:
        await run_command_async(command)
        return output_path
    except Exception:
        return None


async def generate_auto_thumbnail(
    input_path,
    output_path,
):
    input_path = Path(input_path)
    output_path = Path(output_path)

    duration = get_video_duration(
        input_path
    )

    if duration <= 0:
        return None

    timestamp = min(
        5.0,
        max(0.0, duration / 2),
    )

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-2",
        str(output_path),
    ]

    try:
        await run_command_async(command)

        if output_path.exists():
            return output_path

    except Exception:
        pass

    return None


def split_large_file(
    file_path,
    max_size=MAX_SPLIT_SIZE,
):
    file_path = Path(file_path)

    if not file_path.exists():
        return []

    if file_path.stat().st_size <= max_size:
        return [file_path]

    parts = []

    with file_path.open("rb") as source:
        index = 1

        while True:
            chunk = source.read(
                max_size
            )

            if not chunk:
                break

            part_path = file_path.with_name(
                f"{file_path.stem}.part{index:02d}{file_path.suffix}"
            )

            with part_path.open("wb") as destination:
                destination.write(chunk)

            parts.append(part_path)
            index += 1

    return parts


def cleanup_task_artifacts(
    *paths,
):
    for path in paths:
        if not path:
            continue

        try:
            path = Path(path)

            if path.is_file() or path.is_symlink():
                path.unlink(
                    missing_ok=True
                )

            elif path.is_dir():
                shutil.rmtree(
                    path,
                    ignore_errors=True,
                )

        except Exception:
            pass


def update_stats(
    successful=None,
    failed=None,
    downloaded_bytes=None,
):
    if successful is not None:
        STATS["successful"] += int(
            successful
        )

    if failed is not None:
        STATS["failed"] += int(
            failed
        )

    if downloaded_bytes is not None:
        STATS["total_downloaded"] += int(
            downloaded_bytes
        )

    save_stats()
