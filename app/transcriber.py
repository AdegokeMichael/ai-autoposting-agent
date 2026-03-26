"""
Transcription using faster-whisper (local, free, fast).
Returns full transcript with per-word timestamps.
"""
import os
import logging
from faster_whisper import WhisperModel
from app.models import TranscriptSegment

logger = logging.getLogger(__name__)

# Load model once at startup — reuse across requests
_model = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        model_size = os.getenv("WHISPER_MODEL", "base")
        logger.info(f"Loading faster-whisper model: {model_size}")
        # Use int8 for CPU efficiency; use float16 if you have a GPU
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded.")
    return _model


def transcribe_video(video_path: str) -> tuple[str, list[TranscriptSegment]]:
    """
    Transcribe a video file.
    Returns:
        - full_text: complete transcript as a single string
        - segments: list of TranscriptSegment with timestamps
    """
    model = get_model()
    logger.info(f"Transcribing: {video_path}")

    segments_raw, info = model.transcribe(
        video_path,
        beam_size=5,
        word_timestamps=True,  # Get per-word timing
        vad_filter=True,       # Filter out silence
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    segments: list[TranscriptSegment] = []
    full_text_parts = []

    for seg in segments_raw:
        text = seg.text.strip()
        if not text:
            continue
        segments.append(TranscriptSegment(
            start=round(seg.start, 2),
            end=round(seg.end, 2),
            text=text,
        ))
        full_text_parts.append(text)

    full_text = " ".join(full_text_parts)
    logger.info(f"Transcription complete. {len(segments)} segments, {len(full_text)} chars.")
    logger.info(f"Detected language: {info.language} (confidence: {info.language_probability:.2f})")

    return full_text, segments
