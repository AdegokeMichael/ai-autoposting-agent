"""
Folder Watcher - auto-triggers the pipeline when you drop a video into the watch folder.

Usage:
  python -m app.watcher          # runs standalone
  or it's started automatically by run.py if WATCH_FOLDER is set in .env

Drop any video into the configured folder (default: ./watch_inbox/)
and the full pipeline will kick off automatically.
"""
import os
import time
import shutil
import logging
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

logger = logging.getLogger(__name__)

WATCH_DIR = Path(os.getenv("WATCH_FOLDER", "watch_inbox"))
PROCESSED_DIR = Path("watch_processed")
SUPPORTED_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class VideoDropHandler(FileSystemEventHandler):
    """Handles new files dropped into the watch folder."""

    def __init__(self, pipeline_fn):
        self.pipeline_fn = pipeline_fn
        self._processing: set[str] = set()

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED_EXTS:
            logger.debug(f"[Watcher] Ignoring non-video file: {path.name}")
            return

        if str(path) in self._processing:
            return

        # Wait briefly for file write to finish
        logger.info(f"[Watcher] Detected new video: {path.name} — waiting for write to complete...")
        self._processing.add(str(path))

        def _process():
            # Poll until file size stops growing
            prev_size = -1
            stable_count = 0
            for _ in range(60):  # up to 60s wait
                time.sleep(1)
                try:
                    current_size = path.stat().st_size
                except FileNotFoundError:
                    return
                if current_size == prev_size:
                    stable_count += 1
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                prev_size = current_size

            logger.info(f"[Watcher] File stable. Starting pipeline for: {path.name}")

            # Move to uploads dir
            dest = Path("uploads") / path.name
            dest.parent.mkdir(exist_ok=True)
            shutil.move(str(path), str(dest))

            # Move original to processed archive
            PROCESSED_DIR.mkdir(exist_ok=True)

            # Run pipeline
            try:
                self.pipeline_fn(str(dest))
                logger.info(f"[Watcher]  Pipeline complete for: {path.name}")
            except Exception as e:
                logger.error(f"[Watcher]  Pipeline failed for {path.name}: {e}")
            finally:
                self._processing.discard(str(path))

        thread = threading.Thread(target=_process, daemon=True)
        thread.start()


def start_watcher(pipeline_fn) -> Observer:
    """
    Start watching the inbox folder.
    pipeline_fn: callable that takes a video_path string and runs the pipeline.
    Returns the Observer so the caller can stop it later.
    """
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"[Watcher] 👁  Watching folder: {WATCH_DIR.resolve()}")
    logger.info(f"[Watcher] Drop any video here and the pipeline will start automatically.")

    handler = VideoDropHandler(pipeline_fn)
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    return observer


def stop_watcher(observer: Observer):
    observer.stop()
    observer.join()
    logger.info("[Watcher] Stopped.")


# ── Standalone mode ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from app.pipeline import run_pipeline

    obs = start_watcher(run_pipeline)
    print(f"\n👁  Watching: {WATCH_DIR.resolve()}")
    print("Drop a video file in that folder to start the pipeline automatically.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_watcher(obs)
