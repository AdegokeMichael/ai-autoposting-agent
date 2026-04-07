"""
app/platforms/uploadpost.py

Upload-Post (upload-post.com) integration.
Posts to TikTok, Instagram, Facebook, YouTube and more
via Upload-Post's unified API — no TikTok API approval needed.

Why Upload-Post instead of direct TikTok API
─────────────────────────────────────────────
Upload-Post is a verified TikTok partner. They hold the platform
approvals. You connect your TikTok account to their dashboard once,
and from that point all posts flow through their approved access.
No domain name, no developer app, no TikTok business verification required.

Setup (10 minutes, free tier available)
────────────────────────────────────────
1. Create account at https://upload-post.com
2. Connect your TikTok account (@bade.clips) in their dashboard
3. Optionally also connect Instagram and/or Facebook
4. Go to dashboard → API Keys → copy your API key
5. Note your profile name (the identifier used when connecting accounts)
6. Add to .env:
     UPLOADPOST_API_KEY=your_api_key
     UPLOADPOST_PROFILE=your_profile_name

Pricing
────────
Free:  10 uploads/month (enough for testing)
Basic: $16/month — unlimited uploads, 5 profiles
Full pricing at https://upload-post.com/pricing

How it works
─────────────
Sends a multipart POST with the video file directly to Upload-Post's API.
Upload-Post handles the actual TikTok/Instagram/Facebook upload on your behalf.
Supports posting to multiple platforms in a single API call.

Required env vars
──────────────────
UPLOADPOST_API_KEY       Your Upload-Post API key
UPLOADPOST_PROFILE       Your profile name in Upload-Post dashboard
"""
import os
import logging
import httpx
from pathlib import Path
from app.platforms.base import BasePlatform

logger = logging.getLogger(__name__)

UPLOADPOST_API = "https://api.upload-post.com/api"

# Map our internal platform keys to Upload-Post platform strings
_PLATFORM_MAP = {
    "tiktok_uploadpost":    "tiktok",
    "instagram_uploadpost": "instagram",
    "facebook_uploadpost":  "facebook",
    "youtube_uploadpost":   "youtube",
}

_DISPLAY_NAMES = {
    "tiktok_uploadpost":    "TikTok (Upload-Post)",
    "instagram_uploadpost": "Instagram (Upload-Post)",
    "facebook_uploadpost":  "Facebook (Upload-Post)",
    "youtube_uploadpost":   "YouTube (Upload-Post)",
}


class UploadPostPlatform(BasePlatform):

    def __init__(self, service_key: str):
        self._service_key = service_key
        self._up_platform  = _PLATFORM_MAP[service_key]

    @property
    def name(self) -> str:
        return _DISPLAY_NAMES[self._service_key]

    @property
    def key(self) -> str:
        return self._service_key

    def is_configured(self) -> bool:
        api_key = os.getenv("UPLOADPOST_API_KEY", "")
        profile = os.getenv("UPLOADPOST_PROFILE", "")
        return bool(
            api_key and not api_key.startswith("your_") and
            profile and not profile.startswith("your_")
        )

    def _api_key(self) -> str:
        key = os.getenv("UPLOADPOST_API_KEY", "")
        if not key:
            raise ValueError(
                "UPLOADPOST_API_KEY is not set. "
                "Get your key at https://upload-post.com → Dashboard → API Keys."
            )
        return key

    def _profile(self) -> str:
        p = os.getenv("UPLOADPOST_PROFILE", "")
        if not p:
            raise ValueError(
                "UPLOADPOST_PROFILE is not set. "
                "This is the profile name you used when connecting your social accounts in Upload-Post."
            )
        return p

    async def upload_and_post(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str = "",
    ) -> dict:
        """
        Upload a clip to Upload-Post which then posts it to the target platform.

        Upload-Post accepts the video file via multipart form.
        It handles all the platform-specific upload logic on their end.
        """
        api_key = self._api_key()
        profile = self._profile()

        # Build caption — TikTok max 2200 chars
        caption = post_text[:2200]

        # Use topic as title for platforms that need it (YouTube, Reddit)
        post_title = (title or caption.split("\n")[0])[:100]

        logger.info(
            f"[Upload-Post] Posting clip {clip_id} to {self.name} "
            f"(profile: {profile})"
        )

        with open(clip_path, "rb") as video_file:
            video_bytes = video_file.read()

        # Build multipart form data
        # Upload-Post uses platform[] array syntax for multi-platform posting
        form_data = {
            "user":          profile,
            "platform[]":    self._up_platform,
            "title":         caption,              # used as caption on TikTok/Instagram/Facebook
        }

        # Platform-specific title overrides
        if self._up_platform == "youtube":
            form_data["title"]            = post_title
            form_data["youtube_title"]    = post_title
        elif self._up_platform == "tiktok":
            form_data["tiktok_title"]     = caption[:2200]

        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{UPLOADPOST_API}/upload",
                headers={"Authorization": f"Apikey {api_key}"},
                data=form_data,
                files={"video": (Path(clip_path).name, video_bytes, "video/mp4")},
            )

        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"[Upload-Post] API error ({response.status_code}): "
                f"{response.text[:400]}"
            )

        data = response.json()
        logger.info(f"[Upload-Post] Response for clip {clip_id}: {data}")

        # Extract result — Upload-Post returns request_id and status
        request_id = data.get("request_id") or data.get("id", clip_id)
        status = data.get("status", "processing")

        # Get the post URL if available immediately
        post_url = ""
        results = data.get("results", {})
        if isinstance(results, dict):
            platform_result = results.get(self._up_platform, {})
            post_url = platform_result.get("post_url", "") or platform_result.get("url", "")

        logger.info(
            f"[Upload-Post] Clip {clip_id} submitted. "
            f"request_id: {request_id} | status: {status}"
        )

        return {
            "publish_id": request_id,
            "status":     status,
            "url":        post_url or f"https://upload-post.com",
        }