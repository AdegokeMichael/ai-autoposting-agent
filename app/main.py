"""
MrBade AutoPoster — FastAPI Backend
Endpoints:
  POST /upload               → Upload video, start pipeline
  GET  /jobs/{id}            → Check pipeline job status
  GET  /clips                → List all clips (filterable by status)
  GET  /clips/{id}           → Get single clip details
  POST /clips/{id}/approve   → Approve (+ optional caption edit)
  POST /clips/{id}/reject    → Reject clip
  POST /clips/{id}/post      → Post approved clip to TikTok immediately
  GET  /clips/{id}/video     → Stream the clip video
  GET  /clips/{id}/thumb     → Get thumbnail image
  POST /clips/{id}/schedule  → Schedule clip for a specific datetime
  POST /clips/{id}/performance → Record real TikTok stats for hook learning
  GET  /schedule/config      → Get scheduler config
  PATCH /schedule/config     → Update scheduler config
  GET  /schedule/queue       → See queued scheduled posts
  GET  /schedule/next-runs   → See next scheduled posting times
  GET  /analytics            → Dashboard analytics summary
"""
import os
import uuid
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.models import ApprovalAction, ClipStatus, ScheduleConfig
from app import storage
from app.pipeline import run_pipeline
from app.platforms import post_to_platform, get_platform_status, get_configured_platforms
from app.scheduler import start_scheduler, stop_scheduler, update_schedule, get_next_run_times
from app.hook_learner import record_performance, get_analytics_summary
from app.overlays import (
    get_overlay_config, save_overlay_config,
    save_template, delete_template,
    list_archived_templates, restore_template,
    get_active_template,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="MrBade AutoPoster", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

executor = ThreadPoolExecutor(max_workers=2)

# ── Watcher (optional) ────────────────────────────────────────────────────────
_watcher_observer = None

def _maybe_start_watcher():
    global _watcher_observer
    watch_folder = os.getenv("WATCH_FOLDER")
    if watch_folder:
        from app.watcher import start_watcher
        _watcher_observer = start_watcher(run_pipeline)
        logger.info(f"[Watcher] Auto-watching folder: {watch_folder}")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    start_scheduler()
    _maybe_start_watcher()
    logger.info("MrBade AutoPoster v2 started ✅")


@app.on_event("shutdown")
async def on_shutdown():
    stop_scheduler()
    global _watcher_observer
    if _watcher_observer:
        from app.watcher import stop_watcher
        stop_watcher(_watcher_observer)


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html") as f:
        return f.read()


# ── Upload & Pipeline ─────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
        raise HTTPException(400, "Unsupported file type. Use mp4, mov, avi, mkv, or webm.")

    upload_id = str(uuid.uuid4())[:8]
    ext = Path(file.filename).suffix
    save_path = str(UPLOAD_DIR / f"{upload_id}{ext}")

    contents = await file.read()
    with open(save_path, "wb") as f:
        f.write(contents)

    file_size_mb = len(contents) / (1024 * 1024)
    logger.info(f"Video uploaded: {file.filename} ({file_size_mb:.1f} MB) → {save_path}")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, run_pipeline, save_path)

    return {
        "message": "Video uploaded. Pipeline is running!",
        "filename": file.filename,
        "size_mb": round(file_size_mb, 1),
        "note": "Poll GET /clips to see clips as they become ready.",
    }


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ── Clips ─────────────────────────────────────────────────────────────────────

@app.get("/clips")
async def list_clips(status: str = None):
    clips = storage.get_all_clips()
    if status:
        clips = [c for c in clips if c.status.value == status]
    clips.sort(key=lambda c: c.created_at, reverse=True)
    return clips


@app.get("/clips/{clip_id}")
async def get_clip(clip_id: str):
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    return clip


@app.post("/clips/{clip_id}/approve")
async def approve_clip(clip_id: str, action: ApprovalAction = None):
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    if clip.status != ClipStatus.PENDING:
        raise HTTPException(400, f"Clip is not pending (current status: {clip.status})")

    if action and (action.caption or action.hashtags):
        caption = action.caption or clip.caption
        hashtags = action.hashtags or clip.hashtags
        storage.update_clip_content(clip_id, caption, hashtags)

    storage.update_clip_status(clip_id, ClipStatus.APPROVED)
    logger.info(f"Clip {clip_id} approved ✅")
    return {"status": "approved", "clip_id": clip_id}


@app.post("/clips/{clip_id}/reject")
async def reject_clip(clip_id: str):
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    storage.update_clip_status(clip_id, ClipStatus.REJECTED)
    logger.info(f"Clip {clip_id} rejected ❌")
    return {"status": "rejected", "clip_id": clip_id}


@app.post("/clips/{clip_id}/post")
async def post_clip(clip_id: str, platform: str = "youtube"):
    """
    Post an approved clip to a platform.
    platform: tiktok | youtube | instagram | facebook (default: youtube)
    """
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    if clip.status != ClipStatus.APPROVED:
        raise HTTPException(400, "Clip must be approved before posting")

    try:
        result = await post_to_platform(
            platform_key=platform,
            clip_path=clip.clip_path,
            post_text=clip.full_post_text,
            clip_id=clip_id,
            title=clip.topic,
        )
        storage.update_clip_status(
            clip_id,
            ClipStatus.POSTED,
            platform_key=platform,
            publish_id=result.get("publish_id"),
            publish_url=result.get("url"),
        )
        return {
            "status": "posted",
            "platform": result.get("platform_name"),
            "publish_id": result.get("publish_id"),
            "url": result.get("url"),
        }
    except Exception as e:
        storage.update_clip_status(clip_id, ClipStatus.FAILED)
        raise HTTPException(500, f"Posting to {platform} failed: {str(e)}")


@app.get("/platforms")
async def list_platforms():
    """Return all platforms and their configuration status."""
    return get_platform_status()


@app.get("/buffer/channels")
async def list_buffer_channels():
    """
    List all Buffer channels connected to your account.
    Use this to find the Channel IDs to add to .env.
    Requires BUFFER_ACCESS_TOKEN to be set.
    """
    token = os.getenv("BUFFER_ACCESS_TOKEN", "")
    if not token or token.startswith("your_"):
        raise HTTPException(
            400,
            "BUFFER_ACCESS_TOKEN is not set in .env. "
            "Add it first, then call this endpoint to find your Channel IDs."
        )
    from app.platforms.buffer import list_buffer_channels
    try:
        channels = await list_buffer_channels(token)
        return {
            "channels": channels,
            "instructions": (
                "Copy the 'id' for each channel you want to use and add to .env as: "
                "BUFFER_TIKTOK_CHANNEL_ID, BUFFER_INSTAGRAM_CHANNEL_ID, or BUFFER_FACEBOOK_CHANNEL_ID"
            )
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch Buffer channels: {str(e)}")


@app.post("/clips/{clip_id}/performance")
async def record_clip_performance(
    clip_id: str,
    views: int = 0,
    likes: int = 0,
    shares: int = 0,
    comments: int = 0,
    watch_rate: float = 0.0,
):
    """
    Record real TikTok performance stats for a clip.
    This feeds back into Claude's hook scoring to improve future clips.
    watch_rate = decimal 0.0-1.0 (e.g. 0.65 = 65% watch past 3s)
    """
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    record_performance(clip_id, views=views, likes=likes,
                       shares=shares, comments=comments, watch_rate=watch_rate)
    return {"status": "recorded", "clip_id": clip_id, "views": views}


# ── Schedule ──────────────────────────────────────────────────────────────────

@app.get("/schedule/config")
async def get_schedule_config():
    return storage.get_schedule_config()


@app.patch("/schedule/config")
async def patch_schedule_config(config: ScheduleConfig):
    update_schedule(config)
    return {"status": "updated", "config": config}


@app.get("/schedule/queue")
async def get_schedule_queue():
    return storage.get_all_scheduled_posts()


@app.get("/schedule/next-runs")
async def get_next_runs():
    return get_next_run_times()


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/analytics")
async def get_analytics():
    return get_analytics_summary()


@app.get("/ai/provider")
async def get_ai_provider():
    """Return the currently active AI provider and model."""
    from app.ai_brain import get_provider_info
    return get_provider_info()


@app.get("/gdrive/status")
async def get_gdrive_status():
    """Return Google Drive sync status for the dashboard."""
    import subprocess
    log_file = Path("logs/gdrive_sync.log")
    seen_file = Path("logs/gdrive_seen.txt")

    # Check if the sync service is running
    result = subprocess.run(
        ["systemctl", "is-active", "gdrive-sync"],
        capture_output=True, text=True
    )
    service_active = result.stdout.strip() == "active"

    # Get last few log lines
    last_lines = []
    if log_file.exists():
        lines = log_file.read_text().strip().splitlines()
        last_lines = lines[-5:] if lines else []

    # Count files synced
    files_synced = 0
    if seen_file.exists():
        files_synced = len([l for l in seen_file.read_text().splitlines() if l.strip()])

    remote = os.getenv("GDRIVE_REMOTE", "")
    interval = os.getenv("GDRIVE_SYNC_INTERVAL", "30")

    return {
        "service_active": service_active,
        "remote": remote,
        "local_folder": os.getenv("WATCH_FOLDER", ""),
        "sync_interval_seconds": int(interval),
        "total_files_synced": files_synced,
        "recent_log": last_lines,
        "configured": bool(remote),
    }


# ── Overlay / Brand Template ───────────────────────────────────────────────────

@app.get("/overlay/config")
async def get_overlay():
    """Get current overlay configuration and template status."""
    config = get_overlay_config()
    template = get_active_template()
    return {
        **config,
        "template_exists": template is not None,
        "template_path": template,
        "archived_templates": list_archived_templates(),
    }


@app.patch("/overlay/config")
async def update_overlay_config(
    enabled: bool = None,
    similarity: float = None,
):
    """Toggle overlay on/off, adjust colorkey threshold."""
    updates = {}
    if enabled is not None:
        updates["enabled"] = enabled
    if similarity is not None:
        updates["similarity"] = max(0.01, min(0.5, similarity))

    save_overlay_config(updates)
    return {"status": "updated", "config": get_overlay_config()}


@app.post("/overlay/template")
async def upload_template(file: UploadFile = File(...)):
    """
    Upload a new brand template image.
    Accepted formats: PNG, JPG.
    The old template is automatically archived and can be restored.
    Recommended: 1080x1920 PNG (9:16 vertical).
    """
    if not file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "Template must be a PNG or JPG image.")

    contents = await file.read()
    size_kb = len(contents) / 1024

    if size_kb > 10240:  # 10MB limit
        raise HTTPException(400, "Template file is too large. Maximum 10MB.")

    template_path = save_template(contents, file.filename)
    logger.info(f"New brand template uploaded: {file.filename} ({size_kb:.1f}KB)")

    return {
        "status": "uploaded",
        "filename": file.filename,
        "size_kb": round(size_kb, 1),
        "path": template_path,
    }


@app.delete("/overlay/template")
async def remove_template():
    """Delete the active template. Overlay will be skipped until a new one is uploaded."""
    delete_template()
    return {"status": "deleted"}


@app.get("/overlay/template/preview")
async def preview_template():
    """Serve the active template image for preview in the dashboard."""
    template = get_active_template()
    if not template or not Path(template).exists():
        raise HTTPException(404, "No template found")
    media_type = "image/png" if template.endswith(".png") else "image/jpeg"
    return FileResponse(template, media_type=media_type)


@app.post("/overlay/template/restore/{filename}")
async def restore_archived_template(filename: str):
    """Restore a previously archived template."""
    try:
        path = restore_template(filename)
        return {"status": "restored", "path": path}
    except FileNotFoundError:
        raise HTTPException(404, f"Archive not found: {filename}")


# ── Media serving ─────────────────────────────────────────────────────────────

@app.get("/clips/{clip_id}/video")
async def serve_video(clip_id: str):
    clip = storage.get_clip(clip_id)
    if not clip or not Path(clip.clip_path).exists():
        raise HTTPException(404, "Video file not found")
    return FileResponse(clip.clip_path, media_type="video/mp4")


@app.get("/clips/{clip_id}/thumb")
async def serve_thumbnail(clip_id: str):
    clip = storage.get_clip(clip_id)
    if not clip or not clip.thumbnail_path or not Path(clip.thumbnail_path).exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(clip.thumbnail_path, media_type="image/jpeg")


@app.get("/clips/{clip_id}/download")
async def download_clip(clip_id: str):
    """
    Download the clip video file with a clean filename.
    The browser will prompt a Save As dialog.
    """
    clip = storage.get_clip(clip_id)
    if not clip or not Path(clip.clip_path).exists():
        raise HTTPException(404, "Video file not found")

    # Build a clean filename from the topic
    safe_topic = "".join(c if c.isalnum() or c in " -_" else "" for c in clip.topic)
    safe_topic = safe_topic.strip().replace(" ", "_")[:50]
    filename = f"{clip_id}_{safe_topic}.mp4"

    return FileResponse(
        clip.clip_path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/clips/{clip_id}/download-package")
async def download_package(clip_id: str):
    """
    Download a ZIP containing:
      - the video clip (.mp4)
      - caption.txt  (caption text)
      - hashtags.txt (hashtags)
      - post.txt     (full ready-to-paste post text)
      - metadata.json (clip details)
    """
    import zipfile
    import io
    import json
    from fastapi.responses import StreamingResponse

    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    if not Path(clip.clip_path).exists():
        raise HTTPException(404, "Video file not found")

    safe_topic = "".join(c if c.isalnum() or c in " -_" else "" for c in clip.topic)
    safe_topic = safe_topic.strip().replace(" ", "_")[:50]
    zip_filename = f"{clip_id}_{safe_topic}.zip"

    # Build zip in memory
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:

        # Video file
        video_name = f"{clip_id}_{safe_topic}.mp4"
        zf.write(clip.clip_path, video_name)

        # caption.txt
        zf.writestr("caption.txt", clip.caption)

        # hashtags.txt
        hashtag_line = " ".join(f"#{h.lstrip('#')}" for h in clip.hashtags)
        zf.writestr("hashtags.txt", hashtag_line)

        # post.txt — the full ready-to-paste text
        zf.writestr("post.txt", clip.full_post_text)

        # metadata.json
        meta = {
            "clip_id": clip.id,
            "topic": clip.topic,
            "hook_text": clip.hook_text,
            "hook_score": clip.hook_score,
            "duration_seconds": clip.duration,
            "start_time": clip.start_time,
            "end_time": clip.end_time,
            "status": clip.status,
            "created_at": clip.created_at,
            "post_urls": clip.post_urls,
        }
        zf.writestr("metadata.json", json.dumps(meta, indent=2))

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.delete("/clips/{clip_id}")
async def delete_clip(clip_id: str):
    """
    Permanently delete a clip and its files from disk.
    This cannot be undone.
    """
    clip = storage.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")

    deleted = storage.delete_clip(clip_id)
    if not deleted:
        raise HTTPException(500, "Failed to delete clip")

    logger.info(f"Clip {clip_id} deleted by user.")
    return {"status": "deleted", "clip_id": clip_id}
