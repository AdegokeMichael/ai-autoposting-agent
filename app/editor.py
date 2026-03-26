"""
Video editing with FFmpeg.
Cuts clips from source video at precise timestamps.
Also generates thumbnails for the approval UI.
"""
import os
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output/clips")
THUMBNAIL_DIR = Path("output/thumbnails")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)


def cut_clip(
    source_path: str,
    clip_id: str,
    start_time: float,
    end_time: float,
) -> str:
    """
    Cut a clip from source video using FFmpeg.
    Uses re-encoding to ensure clean cuts at exact timestamps.
    Returns the path to the output clip.
    """
    output_path = str(OUTPUT_DIR / f"{clip_id}.mp4")

    duration = end_time - start_time

    cmd = [
        "ffmpeg",
        "-y",                          # Overwrite if exists
        "-ss", str(start_time),        # Start time (before input = fast seek)
        "-i", source_path,             # Input file
        "-t", str(duration),           # Duration
        "-c:v", "libx264",             # Re-encode video for clean cut
        "-c:a", "aac",                 # Re-encode audio
        "-preset", "fast",             # Fast encoding
        "-crf", "23",                  # Quality (18=lossless, 28=low)
        "-movflags", "+faststart",     # Optimise for web playback
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",  # TikTok 9:16
        output_path,
    ]

    logger.info(f"Cutting clip {clip_id}: {start_time:.1f}s → {end_time:.1f}s")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed for clip {clip_id}: {result.stderr[-500:]}")

    logger.info(f"Clip saved: {output_path}")
    return output_path


def generate_thumbnail(clip_path: str, clip_id: str, timestamp: float = 1.0) -> str:
    """
    Extract a thumbnail from a clip at the given timestamp.
    Returns path to thumbnail image.
    """
    thumb_path = str(THUMBNAIL_DIR / f"{clip_id}.jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(timestamp),
        "-i", clip_path,
        "-vframes", "1",
        "-q:v", "2",
        thumb_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"Thumbnail generation failed for {clip_id}: {result.stderr}")
        return ""

    return thumb_path


def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    import json
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
