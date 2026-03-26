"""
Claude-powered analysis engine.
1. Analyzes transcript to find the best viral clip moments
2. Scores hooks (first 0.5s determines watch rate)
3. Writes natural, human-sounding captions + hashtags
"""
import json
import logging
import anthropic
from app.models import ClipSegment, TranscriptSegment

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()

# Lazy import to avoid circular deps
def _get_lessons() -> str:
    try:
        from app.hook_learner import build_hook_lessons
        return build_hook_lessons()
    except Exception:
        return ""

# --- Bade Adesemowo's brand context — feed this to every prompt ---
BRAND_CONTEXT = """
You are helping Bade Adesemowo, a Tech Founder whose niche is:
- Startups & entrepreneurship
- Talent visas (Global Talent Visa, O-1, etc.)
- Being globally attractive as a professional/founder
- Tech, innovation, and building a personal brand
- Opportunities for Africans in the global tech ecosystem

His audience: ambitious professionals, founders, and immigrants who want to build globally.
His tone: direct, confident, motivational, no-fluff. He speaks like a founder who's been through it.
"""

# --- Hook training data — teach Claude what a great hook looks like ---
HOOK_TRAINING = """
HOOK PRINCIPLES (first 0.5 seconds = make or break):
- Pattern interrupts: Say something unexpected or counterintuitive
- Bold claims: "Most people are wrong about X"
- Curiosity gaps: "The one thing nobody tells you about talent visas..."
- Direct address: "If you're a founder trying to move to the UK..."
- Stakes: Make the viewer feel like they'll lose something if they don't watch
- Specificity beats vague: "£50K in 6 months" beats "make money online"

WEAK hooks (avoid): "In this video I'll talk about..." / "Hey guys today we..." / "So I wanted to share..."
STRONG hooks: Start mid-thought, with tension, with a number, with a contradiction
"""


def find_viral_clips(
    full_text: str,
    segments: list[TranscriptSegment],
    video_duration: float,
) -> list[ClipSegment]:
    """
    Ask Claude to find the best moments in the transcript to cut as viral clips.
    Returns a list of ClipSegment with timestamps and hook analysis.
    """
    # Build a timestamped transcript for Claude
    timestamped = "\n".join(
        f"[{seg.start:.1f}s - {seg.end:.1f}s]: {seg.text}"
        for seg in segments
    )

    # Inject real performance lessons if we have them
    lessons = _get_lessons()
    lessons_block = f"\n{lessons}\n" if lessons else ""

    prompt = f"""
{BRAND_CONTEXT}

{HOOK_TRAINING}
{lessons_block}
Below is a timestamped transcript from one of Bade Adesemowo's videos (total duration: {video_duration:.0f} seconds).

TIMESTAMPED TRANSCRIPT:
{timestamped}

YOUR TASK:
Identify 3-6 viral clip opportunities. For each clip:
1. Find a segment where the hook (first sentence spoken) is STRONG
2. The clip should be 15-90 seconds long
3. The clip must deliver ONE clear, valuable insight
4. Clips can overlap in time but should be different angles/hooks

Return ONLY a valid JSON array. No explanation, no markdown, just the JSON:
[
  {{
    "start_time": 12.5,
    "end_time": 45.0,
    "hook_text": "The exact first sentence spoken in this clip",
    "hook_score": 8,
    "hook_reason": "Why this hook will stop the scroll",
    "topic": "One-line topic",
    "value_proposition": "What the viewer gains from watching this clip"
  }}
]
"""

    logger.info("Asking Claude to find viral clips...")
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip any accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    clips_data = json.loads(raw)
    clips = [ClipSegment(**c) for c in clips_data]
    logger.info(f"Claude identified {len(clips)} viral clip candidates.")
    return clips


def write_caption(
    clip_segment: ClipSegment,
    transcript_excerpt: str,
) -> tuple[str, list[str], str]:
    """
    Write a natural, human-sounding TikTok caption for a clip.
    Returns: (caption, hashtags, full_post_text)
    """
    prompt = f"""
{BRAND_CONTEXT}

You need to write a TikTok post caption for the following clip.

CLIP DETAILS:
- Topic: {clip_segment.topic}
- Hook (first line of the clip): {clip_segment.hook_text}
- Why it's valuable: {clip_segment.value_proposition}
- Clip transcript excerpt: {transcript_excerpt}

CAPTION RULES:
1. Write like Bade Adesemowo himself wrote it — direct, no fluff, founder energy
2. DO NOT sound like AI. No "In today's video", no "I hope this helps", no bullet points with emojis as headers
3. First line = the hook (rewrite the hook_text slightly for text format)
4. 2-4 short punchy lines max. Leave space. TikTok is not a blog.
5. End with 1 call to action (follow, comment, share your experience — pick ONE)
6. Then on a new line, 5-8 relevant hashtags

Return ONLY valid JSON:
{{
  "caption": "The caption text only (no hashtags)",
  "hashtags": ["startup", "globaltalent", "talentvisa", "founder", "africaintech"]
}}
"""

    logger.info(f"Writing caption for clip: {clip_segment.topic}")
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    caption = data["caption"]
    hashtags = data["hashtags"]
    full_post_text = caption + "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)

    return caption, hashtags, full_post_text
