"""
Smart Scheduler — APScheduler-based auto-posting engine.

When enabled in settings, it:
1. Runs at configured posting times (default: 8am, 1pm, 7pm Lagos time)
2. Takes the next approved clip from the queue
3. Posts it to TikTok automatically
4. Respects daily limits so you don't flood

Enable via the dashboard Settings tab or PATCH /schedule/config.
"""
import logging
import asyncio
from datetime import datetime, timezone
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from app import storage
from app.models import ScheduledPost, ClipStatus
from app.tiktok import upload_and_post

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_scheduler_started = False


# ── Core posting job ───────────────────────────────────────────────────────────

async def _run_scheduled_posting():
    """
    Called at each scheduled posting time.
    Picks the next approved clip and posts it.
    """
    config = storage.get_schedule_config()
    if not config.enabled:
        return

    # Count how many already posted today
    today = datetime.now(timezone.utc).date()
    todays_posts = [
        p for p in storage.get_all_scheduled_posts()
        if p.status == "posted"
        and p.posted_at
        and p.posted_at[:10] == str(today)
    ]

    if len(todays_posts) >= config.daily_limit:
        logger.info(f"[Scheduler] Daily limit reached ({config.daily_limit}). Skipping.")
        return

    # Get next approved clip (oldest first)
    approved = [c for c in storage.get_all_clips() if c.status == ClipStatus.APPROVED]
    approved.sort(key=lambda c: c.created_at)

    if not approved:
        logger.info("[Scheduler] No approved clips waiting. Nothing to post.")
        return

    clip = approved[0]
    logger.info(f"[Scheduler] Auto-posting clip: {clip.id} — {clip.topic}")

    try:
        result = await upload_and_post(
            clip_path=clip.clip_path,
            post_text=clip.full_post_text,
            clip_id=clip.id,
        )
        storage.update_clip_status(clip.id, ClipStatus.POSTED, tiktok_post_id=result.get("publish_id"))

        # Record in schedule log
        post_record = ScheduledPost(
            id=str(uuid.uuid4())[:8],
            clip_id=clip.id,
            scheduled_at=datetime.now(timezone.utc).isoformat(),
            status="posted",
            posted_at=datetime.now(timezone.utc).isoformat(),
        )
        storage.save_scheduled_post(post_record)
        logger.info(f"[Scheduler]  Posted: {clip.id} | TikTok ID: {result.get('publish_id')}")

    except Exception as e:
        logger.error(f"[Scheduler]  Failed to post {clip.id}: {e}")
        storage.update_clip_status(clip.id, ClipStatus.FAILED)

        post_record = ScheduledPost(
            id=str(uuid.uuid4())[:8],
            clip_id=clip.id,
            scheduled_at=datetime.now(timezone.utc).isoformat(),
            status="failed",
            error=str(e),
        )
        storage.save_scheduled_post(post_record)


# ── Schedule management ────────────────────────────────────────────────────────

def _rebuild_schedule():
    """
    Clear all posting jobs and rebuild from current config.
    Call this whenever config changes.
    """
    global _scheduler_started

    # Remove existing posting jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("post_time_"):
            job.remove()

    config = storage.get_schedule_config()
    if not config.enabled:
        logger.info("[Scheduler] Auto-posting is disabled.")
        return

    try:
        tz = pytz.timezone(config.timezone)
    except Exception:
        tz = pytz.timezone("Africa/Lagos")
        logger.warning(f"[Scheduler] Invalid timezone '{config.timezone}', defaulting to Africa/Lagos")

    for i, time_str in enumerate(config.posting_times):
        try:
            hour, minute = map(int, time_str.split(":"))
            trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
            scheduler.add_job(
                _run_scheduled_posting,
                trigger=trigger,
                id=f"post_time_{i}",
                replace_existing=True,
                name=f"Auto-post at {time_str} ({config.timezone})",
            )
            logger.info(f"[Scheduler] Scheduled posting at {time_str} {config.timezone}")
        except ValueError:
            logger.warning(f"[Scheduler] Invalid time format: '{time_str}' — skipping")

    logger.info(f"[Scheduler] {len(config.posting_times)} posting slots active. Daily limit: {config.daily_limit}")


def start_scheduler():
    """Start the APScheduler. Called once at app startup."""
    global _scheduler_started
    if _scheduler_started:
        return

    scheduler.start()
    _scheduler_started = True
    _rebuild_schedule()
    logger.info("[Scheduler] Engine started.")


def stop_scheduler():
    """Stop the scheduler cleanly at app shutdown."""
    global _scheduler_started
    if _scheduler_started and scheduler.running:
        scheduler.shutdown(wait=False)
        _scheduler_started = False


def update_schedule(config):
    """
    Update schedule config and rebuild jobs.
    Called when user saves settings in dashboard.
    """
    storage.save_schedule_config(config)
    _rebuild_schedule()


def get_next_run_times() -> list[dict]:
    """Return upcoming scheduled posting times for the dashboard."""
    jobs = [j for j in scheduler.get_jobs() if j.id.startswith("post_time_")]
    result = []
    for job in jobs:
        next_run = job.next_run_time
        if next_run:
            result.append({
                "job_id": job.id,
                "name": job.name,
                "next_run": next_run.isoformat(),
            })
    return sorted(result, key=lambda x: x["next_run"])
