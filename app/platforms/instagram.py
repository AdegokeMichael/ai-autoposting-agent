"""
app/platforms/instagram.py

Instagram Reels via Meta Graph API.

Setup:
1. You need a Facebook Business account
2. Connect an Instagram Professional (Creator or Business) account to it
3. Go to https://developers.facebook.com and create an app
4. Add "Instagram Graph API" product
5. Get a long-lived Page Access Token
6. Find your Instagram Business Account ID

Required env vars:
  INSTAGRAM_ACCESS_TOKEN      Long-lived page access token
  INSTAGRAM_ACCOUNT_ID        Your IG Business Account ID

Instagram Reels requirements:
  - Video must be between 3 and 90 seconds
  - Aspect ratio: 9:16 (vertical)
  - Resolution: 1080 x 1920 recommended
  - File must be accessible via a public URL (not a local path)

Because Meta requires a PUBLIC URL for the video file, this platform
uploads the clip to a temporary public location first, then passes
that URL to the Graph API. We use a simple local file server approach
or you can configure an S3 bucket URL.
"""
import os
import logging
import httpx
from app.platforms.base import BasePlatform

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class InstagramPlatform(BasePlatform):

    @property
    def name(self) -> str:
        return "Instagram Reels"

    @property
    def key(self) -> str:
        return "instagram"

    def is_configured(self) -> bool:
        token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        account_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
        return bool(token and account_id and not token.startswith("your_"))

    async def upload_and_post(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str = "",
    ) -> dict:
        """
        Post a Reel to Instagram via the Graph API.

        Meta requires the video to be at a public URL.
        Set INSTAGRAM_PUBLIC_BASE_URL in .env to your server's public URL.
        e.g. http://135.236.211.197:8000
        The clip will be served from /clips/{clip_id}/video
        """
        access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
        account_id = os.getenv("INSTAGRAM_ACCOUNT_ID")
        public_base = os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "").rstrip("/")

        if not access_token or not account_id:
            raise ValueError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID must be set")

        if not public_base:
            raise ValueError(
                "INSTAGRAM_PUBLIC_BASE_URL must be set to your server's public URL. "
                "e.g. http://135.236.211.197:8000"
            )

        video_url = f"{public_base}/clips/{clip_id}/video"
        caption = post_text[:2200]

        async with httpx.AsyncClient(timeout=120) as client:
            # Step 1: Create media container
            logger.info(f"[Instagram] Creating media container for clip {clip_id}")
            container_response = await client.post(
                f"{GRAPH_API_BASE}/{account_id}/media",
                params={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": access_token,
                },
            )
            container_response.raise_for_status()
            container_data = container_response.json()

            if "error" in container_data:
                raise RuntimeError(f"Instagram container error: {container_data['error']}")

            container_id = container_data["id"]
            logger.info(f"[Instagram] Container created: {container_id}")

            # Step 2: Wait for video to process (poll status)
            import asyncio
            for attempt in range(15):
                await asyncio.sleep(5)
                status_response = await client.get(
                    f"{GRAPH_API_BASE}/{container_id}",
                    params={
                        "fields": "status_code,status",
                        "access_token": access_token,
                    },
                )
                status_data = status_response.json()
                status_code = status_data.get("status_code", "")
                logger.info(f"[Instagram] Processing status: {status_code}")

                if status_code == "FINISHED":
                    break
                elif status_code == "ERROR":
                    raise RuntimeError(f"Instagram video processing failed: {status_data}")

            # Step 3: Publish
            logger.info(f"[Instagram] Publishing clip {clip_id}...")
            publish_response = await client.post(
                f"{GRAPH_API_BASE}/{account_id}/media_publish",
                params={
                    "creation_id": container_id,
                    "access_token": access_token,
                },
            )
            publish_response.raise_for_status()
            publish_data = publish_response.json()

            if "error" in publish_data:
                raise RuntimeError(f"Instagram publish error: {publish_data['error']}")

            media_id = publish_data["id"]
            logger.info(f"[Instagram] Posted clip {clip_id} — media_id: {media_id}")

            return {
                "publish_id": media_id,
                "status": "published",
                "url": f"https://www.instagram.com/reel/{media_id}/",
            }
