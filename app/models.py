from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ClipStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"
    FAILED = "failed"


class ClipSegment(BaseModel):
    """A single clip identified by Claude from the transcript"""
    start_time: float        # seconds
    end_time: float          # seconds
    hook_text: str           # The first 0.5s hook sentence
    hook_score: int          # 1-10 rating
    hook_reason: str         # Why this is a strong hook
    topic: str               # What this clip is about
    value_proposition: str   # Why the audience should watch


class GeneratedClip(BaseModel):
    """A fully processed clip ready for review"""
    id: str
    source_video: str
    clip_path: str           # Path to cut video file
    thumbnail_path: Optional[str] = None
    start_time: float
    end_time: float
    duration: float
    hook_text: str
    hook_score: int
    caption: str             # AI-written caption
    hashtags: list[str]
    full_post_text: str      # caption + hashtags combined
    topic: str
    status: ClipStatus = ClipStatus.PENDING
    tiktok_post_id: Optional[str] = None
    created_at: str


class PipelineJob(BaseModel):
    """Tracks a full pipeline run"""
    job_id: str
    source_video: str
    status: str              # processing, complete, failed
    clips_found: int = 0
    clips_generated: list[str] = []  # clip IDs
    error: Optional[str] = None
    created_at: str


class ApprovalAction(BaseModel):
    caption: Optional[str] = None    # Optional edit to caption
    hashtags: Optional[list[str]] = None  # Optional edit to hashtags
    schedule_at: Optional[str] = None    # ISO datetime to schedule posting


class TranscriptSegment(BaseModel):
    """A single word/segment from faster-whisper"""
    start: float
    end: float
    text: str


class ScheduledPost(BaseModel):
    """A clip queued for scheduled posting"""
    id: str
    clip_id: str
    scheduled_at: str       # ISO datetime (UTC)
    status: str = "queued"  # queued, posted, failed, cancelled
    posted_at: Optional[str] = None
    error: Optional[str] = None


class HookPerformance(BaseModel):
    """Tracks how a hook performed after posting — feeds back into Claude"""
    clip_id: str
    hook_text: str
    hook_score: int          # Claude's original prediction
    topic: str
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments: int = 0
    watch_rate: float = 0.0  # % of viewers who watched past 3s
    recorded_at: Optional[str] = None


class ScheduleConfig(BaseModel):
    """Posting schedule settings"""
    enabled: bool = False
    daily_limit: int = 3                  # Max posts per day
    posting_times: list[str] = ["08:00", "13:00", "19:00"]  # UTC times
    timezone: str = "Africa/Lagos"


class AnalyticsSummary(BaseModel):
    """Aggregated stats for the dashboard"""
    total_clips: int = 0
    pending: int = 0
    approved: int = 0
    posted: int = 0
    rejected: int = 0
    avg_hook_score: float = 0.0
    top_topics: list[str] = []
    scheduled_queue: int = 0
