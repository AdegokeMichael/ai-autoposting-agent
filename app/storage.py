"""
Simple JSON-based storage for clips and jobs.
No database needed — keeps everything in local files.
"""
import json
import logging
from pathlib import Path
from typing import Optional
from app.models import GeneratedClip, PipelineJob, ClipStatus, ScheduledPost, HookPerformance, ScheduleConfig

logger = logging.getLogger(__name__)

STORE_DIR = Path("output/store")
STORE_DIR.mkdir(parents=True, exist_ok=True)

CLIPS_FILE = STORE_DIR / "clips.json"
JOBS_FILE = STORE_DIR / "jobs.json"


def _load(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _save(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── Clips ──────────────────────────────────────────────


def save_clip(clip: GeneratedClip):
    data = _load(CLIPS_FILE)
    data[clip.id] = clip.model_dump()
    _save(CLIPS_FILE, data)


def get_clip(clip_id: str) -> Optional[GeneratedClip]:
    data = _load(CLIPS_FILE)
    if clip_id in data:
        return GeneratedClip(**data[clip_id])
    return None


def get_all_clips() -> list[GeneratedClip]:
    data = _load(CLIPS_FILE)
    return [GeneratedClip(**v) for v in data.values()]


def get_pending_clips() -> list[GeneratedClip]:
    return [c for c in get_all_clips() if c.status == ClipStatus.PENDING]


def delete_clip(clip_id: str) -> bool:
    """
    Remove a clip from storage and delete its files from disk.
    Returns True if deleted, False if not found.
    """
    import shutil
    data = _load(CLIPS_FILE)
    if clip_id not in data:
        return False

    clip_data = data[clip_id]

    # Delete video file
    clip_path = clip_data.get("clip_path")
    if clip_path and Path(clip_path).exists():
        Path(clip_path).unlink()

    # Delete thumbnail
    thumb_path = clip_data.get("thumbnail_path")
    if thumb_path and Path(thumb_path).exists():
        Path(thumb_path).unlink()

    # Remove from store
    del data[clip_id]
    _save(CLIPS_FILE, data)
    logger.info(f"Deleted clip {clip_id} and its files.")
    return True


def update_clip_status(clip_id: str, status: ClipStatus, tiktok_post_id: str = None, platform_key: str = None, publish_id: str = None, publish_url: str = None):
    data = _load(CLIPS_FILE)
    if clip_id in data:
        data[clip_id]["status"] = status.value
        # Legacy TikTok field
        if tiktok_post_id:
            data[clip_id]["tiktok_post_id"] = tiktok_post_id
        # Multi-platform tracking
        if platform_key and publish_id:
            if "post_ids" not in data[clip_id]:
                data[clip_id]["post_ids"] = {}
            if "post_urls" not in data[clip_id]:
                data[clip_id]["post_urls"] = {}
            data[clip_id]["post_ids"][platform_key] = publish_id
            if publish_url:
                data[clip_id]["post_urls"][platform_key] = publish_url
        _save(CLIPS_FILE, data)


def update_clip_content(clip_id: str, caption: str, hashtags: list[str]):
    data = _load(CLIPS_FILE)
    if clip_id in data:
        data[clip_id]["caption"] = caption
        data[clip_id]["hashtags"] = hashtags
        data[clip_id]["full_post_text"] = (
            caption + "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        )
        _save(CLIPS_FILE, data)


# ── Jobs ──────────────────────────────────────────────


def save_job(job: PipelineJob):
    data = _load(JOBS_FILE)
    data[job.job_id] = job.model_dump()
    _save(JOBS_FILE, data)


def get_job(job_id: str) -> Optional[PipelineJob]:
    data = _load(JOBS_FILE)
    if job_id in data:
        return PipelineJob(**data[job_id])
    return None


def update_job(job_id: str, **kwargs):
    data = _load(JOBS_FILE)
    if job_id in data:
        data[job_id].update(kwargs)
        _save(JOBS_FILE, data)


# ── Scheduled Posts ────────────────────────────────────

SCHEDULE_FILE = STORE_DIR / "schedule.json"


def save_scheduled_post(post: ScheduledPost):
    data = _load(SCHEDULE_FILE)
    data[post.id] = post.model_dump()
    _save(SCHEDULE_FILE, data)


def get_scheduled_post(post_id: str) -> Optional[ScheduledPost]:
    data = _load(SCHEDULE_FILE)
    if post_id in data:
        return ScheduledPost(**data[post_id])
    return None


def get_all_scheduled_posts() -> list[ScheduledPost]:
    data = _load(SCHEDULE_FILE)
    return [ScheduledPost(**v) for v in data.values()]


def get_queued_posts() -> list[ScheduledPost]:
    return [p for p in get_all_scheduled_posts() if p.status == "queued"]


def update_scheduled_post(post_id: str, **kwargs):
    data = _load(SCHEDULE_FILE)
    if post_id in data:
        data[post_id].update(kwargs)
        _save(SCHEDULE_FILE, data)


def cancel_scheduled_post(post_id: str):
    update_scheduled_post(post_id, status="cancelled")


# ── Hook Performance ───────────────────────────────────

HOOK_PERF_FILE = STORE_DIR / "hook_performance.json"


def save_hook_performance(perf: HookPerformance):
    data = _load(HOOK_PERF_FILE)
    data[perf.clip_id] = perf.model_dump()
    _save(HOOK_PERF_FILE, data)


def get_all_hook_performances() -> list[HookPerformance]:
    data = _load(HOOK_PERF_FILE)
    return [HookPerformance(**v) for v in data.values()]


def get_top_hooks(limit: int = 10) -> list[HookPerformance]:
    """Return top performing hooks by view count."""
    perfs = get_all_hook_performances()
    return sorted(perfs, key=lambda p: p.views, reverse=True)[:limit]


# ── Schedule Config ────────────────────────────────────

CONFIG_FILE = STORE_DIR / "config.json"


def get_schedule_config() -> ScheduleConfig:
    data = _load(CONFIG_FILE)
    if "schedule" in data:
        return ScheduleConfig(**data["schedule"])
    return ScheduleConfig()


def save_schedule_config(config: ScheduleConfig):
    data = _load(CONFIG_FILE)
    data["schedule"] = config.model_dump()
    _save(CONFIG_FILE, data)