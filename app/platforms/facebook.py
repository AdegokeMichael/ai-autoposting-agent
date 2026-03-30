"""
app/platforms/facebook.py

Facebook Reels via Meta Graph API.

Setup:
1. Facebook Business account required
2. Create an app at https://developers.facebook.com
3. Add "Pages API" product
4. Get a long-lived Page Access Token
5. Find your Facebook Page ID

Required env vars:
  FACEBOOK_ACCESS_TOKEN    Long-lived Page Access Token
  FACEBOOK_PAGE_ID         Your Facebook Page ID

Same public URL requirement as Instagram — the video must be
accessible at a public URL. Uses FACEBOOK_PUBLIC_BASE_URL or
falls back to INSTAGRAM_PUBLIC_BASE_URL if both are on same server.
"""
import os
import logging
import httpx
from app.platforms.base import BasePlatform

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


class FacebookPlatform(BasePlatform):

    @property
    def name(self) -> str:
        return "Facebook Reels"

    @property
    def key(self) -> str:
        return "facebook"

    def is_configured(self) -> bool:
        token = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
        page_id = os.getenv("FACEBOOK_PAGE_ID", "")
        return bool(token and page_id and not token.startswith("your_"))

    async def upload_and_post(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str = "",
    ) -> dict:
        access_token = os.getenv("FACEBOOK_ACCESS_TOKEN")
        page_id = os.getenv("FACEBOOK_PAGE_ID")
        public_base = os.getenv(
            "FACEBOOK_PUBLIC_BASE_URL",
            os.getenv("INSTAGRAM_PUBLIC_BASE_URL", "")
        ).rstrip("/")

        if not access_token or not page_id:
            raise ValueError("FACEBOOK_ACCESS_TOKEN and FACEBOOK_PAGE_ID must be set")

        if not public_base:
            raise ValueError("FACEBOOK_PUBLIC_BASE_URL must be set to your server's public URL")

        video_url = f"{public_base}/clips/{clip_id}/video"
        description = post_text[:63206]  # Facebook's limit

        async with httpx.AsyncClient(timeout=180) as client:
            # Step 1: Initialise resumable upload
            logger.info(f"[Facebook] Starting upload for clip {clip_id}")
            init_response = await client.post(
                f"{GRAPH_API_BASE}/{page_id}/video_reels",
                params={"upload_phase": "start", "access_token": access_token},
            )
            init_response.raise_for_status()
            init_data = init_response.json()

            if "error" in init_data:
                raise RuntimeError(f"Facebook init error: {init_data['error']}")

            video_id = init_data["video_id"]
            upload_url = init_data["upload_url"]
            logger.info(f"[Facebook] video_id: {video_id}")

            # Step 2: Upload video bytes
            with open(clip_path, "rb") as f:
                video_bytes = f.read()

            upload_response = await client.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "Content-Type": "application/octet-stream",
                },
                content=video_bytes,
            )
            upload_response.raise_for_status()
            logger.info(f"[Facebook] Upload complete for clip {clip_id}")

            # Step 3: Publish the Reel
            publish_response = await client.post(
                f"{GRAPH_API_BASE}/{page_id}/video_reels",
                params={
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "access_token": access_token,
                    "video_state": "PUBLISHED",
                    "description": description,
                },
            )
            publish_response.raise_for_status()
            publish_data = publish_response.json()

            if "error" in publish_data:
                raise RuntimeError(f"Facebook publish error: {publish_data['error']}")

            logger.info(f"[Facebook] Posted clip {clip_id} — video_id: {video_id}")
            return {
                "publish_id": video_id,
                "status": "published",
                "url": f"https://www.facebook.com/reel/{video_id}",
            }
