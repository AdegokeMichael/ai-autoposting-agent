"""
app/platforms/youtube.py

YouTube Shorts via YouTube Data API v3.
No review process — credentials are instant via Google Cloud Console.

Setup (takes ~10 minutes):
1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable "YouTube Data API v3"
4. Go to APIs & Services > Credentials
5. Create OAuth 2.0 Client ID (Desktop app type)
6. Download the client_secret JSON file
7. Run: python cli.py youtube-auth
   This opens a browser, you log in to the YouTube channel,
   and it saves a token file locally.

Required env vars:
  YOUTUBE_CLIENT_SECRETS_FILE   path to downloaded client_secret JSON
  YOUTUBE_TOKEN_FILE            path where token will be saved (default: youtube_token.json)
  YOUTUBE_CHANNEL_ID            your channel ID (optional, for logging)

Videos under 60 seconds with 9:16 ratio are automatically
classified as Shorts by YouTube. No special flag needed.
"""
import os
import json
import logging
import pickle
from pathlib import Path
from app.platforms.base import BasePlatform

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000


class YouTubePlatform(BasePlatform):

    @property
    def name(self) -> str:
        return "YouTube Shorts"

    @property
    def key(self) -> str:
        return "youtube"

    def is_configured(self) -> bool:
        secrets_file = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "")
        token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
        return (
            bool(secrets_file) and
            Path(secrets_file).exists() and
            Path(token_file).exists()
        )

    def _get_credentials(self):
        """Load OAuth credentials from token file, refresh if expired."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")
        creds = None

        if Path(token_file).exists():
            with open(token_file, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_file, "wb") as f:
                    pickle.dump(creds, f)
            else:
                raise RuntimeError(
                    "YouTube token is missing or expired. "
                    "Run: python cli.py youtube-auth to re-authenticate."
                )
        return creds

    def _build_service(self):
        from googleapiclient.discovery import build
        creds = self._get_credentials()
        return build("youtube", "v3", credentials=creds)

    async def upload_and_post(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str = "",
    ) -> dict:
        """
        Upload a clip to YouTube as a Short.
        Videos under 60s with 9:16 ratio are auto-classified as Shorts.
        """
        import asyncio
        from googleapiclient.http import MediaFileUpload

        # Run the blocking Google API call in a thread executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._blocking_upload(clip_path, post_text, clip_id, title)
        )
        return result

    def _blocking_upload(
        self,
        clip_path: str,
        post_text: str,
        clip_id: str,
        title: str,
    ) -> dict:
        from googleapiclient.http import MediaFileUpload

        youtube = self._build_service()

        # Use first line of caption as title, rest as description
        lines = post_text.strip().split("\n")
        video_title = (title or lines[0])[:MAX_TITLE_LENGTH]
        description = post_text[:MAX_DESCRIPTION_LENGTH]

        # Add #Shorts to description so YouTube classifies it correctly
        if "#Shorts" not in description and "#shorts" not in description:
            description += "\n\n#Shorts"

        body = {
            "snippet": {
                "title": video_title,
                "description": description,
                "categoryId": "22",          # People & Blogs
                "tags": ["Shorts", "startup", "founder", "talentvisa"],
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            clip_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=4 * 1024 * 1024,  # 4MB chunks
        )

        logger.info(f"[YouTube] Uploading clip {clip_id}...")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"[YouTube] Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        video_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info(f"[YouTube] Posted clip {clip_id} — video_id: {video_id} — {video_url}")

        return {
            "publish_id": video_id,
            "status": "published",
            "url": video_url,
        }


def run_youtube_auth():
    """
    Run this once to authenticate with YouTube.
    Opens a browser window — log in to the channel you want to post to.
    Saves a token file that the app uses for all future uploads.
    Called via: python cli.py youtube-auth
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets_file = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE")
    if not secrets_file or not Path(secrets_file).exists():
        print(f"\n[YouTube Auth] ERROR: YOUTUBE_CLIENT_SECRETS_FILE not set or file not found.")
        print(f"  Download your client_secret JSON from Google Cloud Console")
        print(f"  and set YOUTUBE_CLIENT_SECRETS_FILE=/path/to/client_secret.json in .env\n")
        return

    token_file = os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json")

    flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_file, "wb") as f:
        pickle.dump(creds, f)

    print(f"\n[YouTube Auth] Authentication successful!")
    print(f"  Token saved to: {token_file}")
    print(f"  You can now post to YouTube via the dashboard.\n")
