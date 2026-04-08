"""
Pipeline orchestrator.
Runs the full flow: transcribe → analyze → cut → caption → store
"""
import os
import uuid
import logging
from datetime import datetime
from pathlib import Path

from app.models import GeneratedClip, PipelineJob, ClipStatus, TranscriptSegment
from app.transcriber import transcribe_video
from app.analyzer import find_viral_clips, write_caption
from app.editor import cut_clip, generate_thumbnail, get_video_duration
from app.overlays import apply_overlay
from app import storage

logger = logging.getLogger(__name__)


def run_pipeline(video_path: str) -> PipelineJob:
    """
    Full pipeline from video file → clips ready for approval.
    Runs synchronously (call from background thread).
    """
    job_id = str(uuid.uuid4())[:8]
    job = PipelineJob(
        job_id=job_id,
        source_video=video_path,
        status="processing",
        created_at=datetime.utcnow().isoformat(),
    )
    storage.save_job(job)
    logger.info(f"[Job {job_id}] Pipeline started for: {video_path}")

    try:
        # ── Step 1: Get video duration ──────────────────
        logger.info(f"[Job {job_id}] Getting video duration...")
        duration = get_video_duration(video_path)
        logger.info(f"[Job {job_id}] Duration: {duration:.1f}s")

        # ── Step 2: Transcribe with faster-whisper ──────
        logger.info(f"[Job {job_id}] Transcribing with faster-whisper...")
        full_text, segments = transcribe_video(video_path)
        logger.info(f"[Job {job_id}] Transcript: {len(full_text)} chars, {len(segments)} segments")

        # ── Step 3: Claude finds viral clip moments ──────
        logger.info(f"[Job {job_id}] Claude analyzing for viral clips...")
        clip_candidates = find_viral_clips(full_text, segments, duration)
        logger.info(f"[Job {job_id}] Found {len(clip_candidates)} clip candidates")

        # Enforce minimum clip duration — extend any short clips
        MIN_DURATION = float(os.getenv("MIN_CLIP_DURATION", "40"))
        for c in clip_candidates:
            clip_len = c.end_time - c.start_time
            if clip_len < MIN_DURATION:
                logger.warning(
                    f"[Job {job_id}] Clip '{c.topic}' is only {clip_len:.1f}s — "
                    f"extending end_time to meet {MIN_DURATION}s minimum"
                )
                c.end_time = min(c.start_time + MIN_DURATION, duration)

        # ── Step 4: Cut each clip + write captions ───────
        generated_clip_ids = []

        for i, candidate in enumerate(clip_candidates):
            clip_id = f"{job_id}-clip{i+1}"
            logger.info(f"[Job {job_id}] Processing clip {i+1}: {candidate.topic}")

            # Cut the video
            clip_path = cut_clip(
                source_path=video_path,
                clip_id=clip_id,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
            )

            # Apply branded outro card (if enabled and template exists)
            clip_path = apply_overlay(clip_path, clip_id)

            # Generate thumbnail
            thumb_path = generate_thumbnail(clip_path, clip_id)

            # Get the transcript excerpt for this clip
            excerpt = _get_excerpt(segments, candidate.start_time, candidate.end_time)

            # Claude writes the caption
            caption, hashtags, full_post_text = write_caption(candidate, excerpt)

            # Save clip
            clip = GeneratedClip(
                id=clip_id,
                source_video=video_path,
                clip_path=clip_path,
                thumbnail_path=thumb_path,
                start_time=candidate.start_time,
                end_time=candidate.end_time,
                duration=round(candidate.end_time - candidate.start_time, 1),
                hook_text=candidate.hook_text,
                hook_score=candidate.hook_score,
                caption=caption,
                hashtags=hashtags,
                full_post_text=full_post_text,
                topic=candidate.topic,
                status=ClipStatus.PENDING,
                created_at=datetime.utcnow().isoformat(),
            )
            storage.save_clip(clip)
            generated_clip_ids.append(clip_id)
            logger.info(f"[Job {job_id}] Clip {clip_id} ready for review. Hook score: {candidate.hook_score}/10")

        # ── Step 5: Mark job complete ────────────────────
        storage.update_job(
            job_id,
            status="complete",
            clips_found=len(clip_candidates),
            clips_generated=generated_clip_ids,
        )
        logger.info(f"[Job {job_id}] ✅ Pipeline complete. {len(generated_clip_ids)} clips ready for your approval.")

        return storage.get_job(job_id)

    except Exception as e:
        logger.error(f"[Job {job_id}] Pipeline failed: {e}", exc_info=True)
        storage.update_job(job_id, status="failed", error=str(e))
        raise


def _get_excerpt(segments: list[TranscriptSegment], start: float, end: float) -> str:
    """Get transcript text between two timestamps."""
    relevant = [s.text for s in segments if s.start >= start and s.end <= end]
    return " ".join(relevant)[:1000]  # Cap at 1000 chars for the prompt
