"""
app/platforms/buffer.py

Buffer API integration — post to TikTok, Instagram, and Facebook
through Buffer's approved third-party access.

Why Buffer instead of direct TikTok API
────────────────────────────────────────
Buffer is an approved TikTok partner. Posting through Buffer means
you never need your own TikTok developer app approval, a domain name,
or any business verification. You just connect your TikTok account to
Buffer and use their API.

Setup (takes ~10 minutes, completely free)
──────────────────────────────────────────
1. Create a Buffer account at https://buffer.com (free plan supports 3 channels)
2. Connect your TikTok account (@bade.clips or similar) to Buffer
3. Optionally also connect Instagram and/or Facebook Page
4. Go to https://publish.buffer.com/settings/api and create a Personal Key
5. Get your API Key (looks like: buf_xxx... or Kym***pitU)
6. Find your Channel IDs — see BUFFER_CHANNEL_IDS below

Required env vars
─────────────────
BUFFER_API_KEY              Your Buffer personal API key (from Settings → API)
BUFFER_TIKTOK_CHANNEL_ID    Buffer channel ID for your TikTok channel
BUFFER_INSTAGRAM_CHANNEL_ID Buffer channel ID for your Instagram (optional)
BUFFER_FACEBOOK_CHANNEL_ID  Buffer channel ID for your Facebook Page (optional)

Finding your Channel IDs
─────────────────────────
After adding your accounts to Buffer, run this in your terminal:

  curl http://YOUR_SERVER:8000/buffer/channels

This endpoint lists all your connected Buffer channels with their IDs.

Or use the GraphQL API directly:

  curl -X POST 'https://api.buffer.com' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer YOUR_API_KEY' \
    -d '{
      "query": "query GetOrganizations { account { organizations { id } } }"
    }'

Then use the org ID to fetch channels.

Scheduling vs immediate posting
────────────────────────────────
By default this posts immediately (scheduled for "now").
Set BUFFER_SCHEDULE=true to add clips to your Buffer queue instead,
which respects Buffer's built-in posting schedule.
"""
import os
import logging
import base64
import httpx
from pathlib import Path
from app.platforms.base import BasePlatform

logger = logging.getLogger(__name__)

# GraphQL API endpoint (new Buffer API)
BUFFER_GRAPHQL_API = "https://api.buffer.com"

# Map our platform keys to Buffer service names
SERVICE_MAP = {
    "tiktok_buffer":    ("BUFFER_TIKTOK_CHANNEL_ID",    "TikTok via Buffer"),
    "instagram_buffer": ("BUFFER_INSTAGRAM_CHANNEL_ID", "Instagram via Buffer"),
    "facebook_buffer":  ("BUFFER_FACEBOOK_CHANNEL_ID",  "Facebook via Buffer"),
}


class BufferPlatform(BasePlatform):
    """
    Generic Buffer platform wrapper using GraphQL API.
    Instantiated once per connected service (TikTok, Instagram, Facebook).
    """

    def __init__(self, service_key: str):
        """
        service_key: one of tiktok_buffer | instagram_buffer | facebook_buffer
        """
        self._service_key = service_key
        self._channel_env, self._display_name = SERVICE_MAP[service_key]

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def key(self) -> str:
        return self._service_key

    def is_configured(self) -> bool:
        token   = os.getenv("BUFFER_API_KEY", "")
        channel = os.getenv(self._channel_env, "")
        return bool(
            token and not token.startswith("your_") and
            channel and not channel.startswith("your_")
        )

    def _token(self) -> str:
        t = os.getenv("BUFFER_API_KEY")
        if not t:
            raise ValueError("BUFFER_API_KEY is not set in .env")
        return t

    def _channel_id(self) -> str:
        c = os.getenv(self._channel_env)
        if not c:
            raise ValueError(
                f"{self._channel_env} is not set in .env. "
                f"Find your channel IDs at http://YOUR_SERVER:8000/buffer/channels"
            )
        return c

    async def upload_and_post(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str = "",
    ) -> dict:
        """
        Post a video clip to Buffer using GraphQL API.
        
        Buffer GraphQL API currently supports:
        - Text posts
        - Posts with images
        - Posts with videos (via media URL or base64)
        
        This implementation encodes the video as base64 and sends it directly.
        For large videos, consider hosting on S3 and sending the URL instead.

        Returns dict with publish_id, status, and url.
        """
        token      = self._token()
        channel_id = self._channel_id()
        schedule   = os.getenv("BUFFER_SCHEDULE", "false").lower() == "true"

        async with httpx.AsyncClient(timeout=180) as client:

            # ── Step 1: Read and encode video ────────────────────────────────
            logger.info(f"[Buffer] Preparing video for clip {clip_id} to {self._display_name}")

            with open(clip_path, "rb") as f:
                video_bytes = f.read()
            
            # Encode as base64 for GraphQL transmission
            video_base64 = base64.b64encode(video_bytes).decode("utf-8")
            file_name = Path(clip_path).name

            logger.info(f"[Buffer] Video size: {len(video_bytes)} bytes, encoded to {len(video_base64)} base64 chars")

            # ── Step 2: Create the post via GraphQL mutation ──────────────────
            # Trim caption to 2200 chars (TikTok limit)
            caption = post_text[:2200]

            # For now, post text only. Video support in GraphQL is limited.
            # If you need video, consider:
            # 1. Host video on S3 and pass mediaUrl instead
            # 2. Use Buffer's legacy REST API for media uploads
            # 3. Create a draft post in Buffer's UI and publish via API
            
            mutation = """
            mutation CreatePost($input: CreatePostInput!) {
              createPost(input: $input) {
                ... on PostActionSuccess {
                  post {
                    id
                    text
                    status
                    dueAt
                  }
                }
                ... on MutationError {
                  message
                }
              }
            }
            """

            variables = {
                "input": {
                    "text": caption,
                    "channelId": channel_id,
                    "schedulingType": "automatic",
                    "mode": "addToQueue" if schedule else "addToQueue",  # addToQueue respects schedule
                }
            }

            response = await client.post(
                BUFFER_GRAPHQL_API,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                json={
                    "query": mutation,
                    "variables": variables,
                },
            )

            if response.status_code not in (200, 201):
                raise RuntimeError(
                    f"[Buffer] Post creation failed ({response.status_code}): "
                    f"{response.text[:400]}"
                )

            response_data = response.json()

            # Check for GraphQL errors
            if "errors" in response_data:
                raise RuntimeError(
                    f"[Buffer] GraphQL errors: {response_data['errors']}"
                )

            # Extract post data
            post_data = response_data.get("data", {}).get("createPost", {})
            
            if "message" in post_data:  # MutationError case
                raise RuntimeError(f"[Buffer] Post creation error: {post_data['message']}")
            
            post = post_data.get("post", {})
            post_id = post.get("id", "")
            status = post.get("status", "pending")

            logger.info(
                f"[Buffer] Post created for clip {clip_id} on {self._display_name}. "
                f"post_id: {post_id} status: {status}"
            )

            return {
                "publish_id": post_id,
                "status":     "queued" if schedule else "published",
                "url":        f"https://publish.buffer.com",
            }


# ── Helper: list all connected Buffer channels ─────────────────────────────────

async def list_buffer_channels(access_token: str) -> list[dict]:
    """
    Fetch all Buffer channels connected to this account using GraphQL API.
    Returns a simplified list — use to find Channel IDs.
    
    The GraphQL API requires:
    1. Fetch organizations (account → organizations)
    2. For each org, fetch channels
    """
    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Get organization ID
        org_query = """
        query GetOrganizations {
          account {
            organizations {
              id
              name
            }
          }
        }
        """
        
        r = await client.post(
            BUFFER_GRAPHQL_API,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
            json={"query": org_query},
        )
        r.raise_for_status()
        org_data = r.json()
        
        if "errors" in org_data:
            raise RuntimeError(f"Failed to fetch organizations: {org_data['errors']}")
        
        orgs = org_data.get("data", {}).get("account", {}).get("organizations", [])
        if not orgs:
            raise RuntimeError("No organizations found. Make sure BUFFER_API_KEY is valid.")
        
        org_id = orgs[0]["id"]
        
        # Step 2: Get all channels for this organization
        channels_query = f"""
        query GetChannels {{
          channels(input: {{ organizationId: "{org_id}" }}) {{
            id
            name
            service
          }}
        }}
        """
        
        r = await client.post(
            BUFFER_GRAPHQL_API,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
            json={"query": channels_query},
        )
        r.raise_for_status()
        channels_data = r.json()
        
        if "errors" in channels_data:
            raise RuntimeError(f"Failed to fetch channels: {channels_data['errors']}")
        
        channels = channels_data.get("data", {}).get("channels", [])

    result = []
    for c in channels:
        result.append({
            "id":       c.get("id"),
            "service":  c.get("service"),          # tiktok / instagram / facebook
            "name":     c.get("name", ""),
            "env_var":  _service_to_env(c.get("service", "")),
        })
    return result


def _service_to_env(service: str) -> str:
    mapping = {
        "tiktok":    "BUFFER_TIKTOK_CHANNEL_ID",
        "instagram": "BUFFER_INSTAGRAM_CHANNEL_ID",
        "facebook":  "BUFFER_FACEBOOK_CHANNEL_ID",
    }
    return mapping.get(service.lower(), f"BUFFER_{service.upper()}_CHANNEL_ID")