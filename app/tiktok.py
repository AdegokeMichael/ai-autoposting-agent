"""
TikTok Content Posting API integration.
Requires TikTok Business Account with Content Posting API access.

Setup steps:
1. Register at developers.tiktok.com
2. Create an app with "Content Posting API" scope
3. Complete business verification (2-3 days)
4. Get your access token via OAuth
"""
import os
import logging
import httpx
from pathlib import Path

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


async def upload_and_post(
    clip_path: str,
    post_text: str,
    clip_id: str,
) -> dict:
    """
    Upload a video clip and post it to TikTok.
    Uses the TikTok Content Posting API (file upload flow).

    Returns dict with post_id and status.
    """
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
    if not access_token:
        raise ValueError("TIKTOK_ACCESS_TOKEN not set in environment")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    clip_size = Path(clip_path).stat().st_size

    async with httpx.AsyncClient(timeout=120) as client:
        # Step 1: Initialize upload
        logger.info(f"Initializing TikTok upload for clip {clip_id}")
        init_payload = {
            "post_info": {
                "title": post_text[:2200],   # TikTok caption limit
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": clip_size,
                "chunk_size": clip_size,   # Single chunk for files < 64MB
                "total_chunk_count": 1,
            },
        }

        init_response = await client.post(
            f"{TIKTOK_API_BASE}/post/publish/video/init/",
            headers=headers,
            json=init_payload,
        )
        init_response.raise_for_status()
        init_data = init_response.json()

        if init_data.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"TikTok init failed: {init_data}")

        publish_id = init_data["data"]["publish_id"]
        upload_url = init_data["data"]["upload_url"]
        logger.info(f"TikTok publish_id: {publish_id}")

        # Step 2: Upload video bytes
        with open(clip_path, "rb") as f:
            video_bytes = f.read()

        upload_headers = {
            "Content-Range": f"bytes 0-{clip_size - 1}/{clip_size}",
            "Content-Length": str(clip_size),
            "Content-Type": "video/mp4",
        }

        upload_response = await client.put(
            upload_url,
            content=video_bytes,
            headers=upload_headers,
        )
        upload_response.raise_for_status()
        logger.info(f"Video uploaded. Status: {upload_response.status_code}")

        # Step 3: Check publish status
        status_payload = {"publish_id": publish_id}
        status_response = await client.post(
            f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
            headers=headers,
            json=status_payload,
        )
        status_response.raise_for_status()
        status_data = status_response.json()

        return {
            "publish_id": publish_id,
            "status": status_data.get("data", {}).get("status", "PROCESSING"),
            "raw": status_data,
        }


async def check_post_status(publish_id: str) -> str:
    """Check the status of a published post."""
    access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
        )
        data = response.json()
        return data.get("data", {}).get("status", "UNKNOWN")
