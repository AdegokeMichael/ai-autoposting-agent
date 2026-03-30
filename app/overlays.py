"""
app/overlays.py

Branded frame compositing system.

How it works
────────────
The template PNG acts as a frame. It has three zones:

  ┌──────────────────────────────┐  y=0
  │  Header  (white / opaque)    │  Logo, title area
  ├──────────────────────────────┤  y=354
  │                              │
  │  Video region  (pure black)  │  Video plays here
  │                              │
  ├──────────────────────────────┤  y=1766
  │  Brand card  (green/opaque)  │  eMigr8 info, always visible
  └──────────────────────────────┘  y=1920

FFmpeg colorkey composite:
  1. The clip (already 1080×1920) is used as the base layer.
  2. The template is overlaid on top.
  3. Pure-black pixels in the template are made transparent (colorkey).
  4. Result: video shows through the black zone, brand stays on top everywhere.

Template requirements for the design team
─────────────────────────────────────────
  • Format: PNG (preferred) or JPG
  • Size: 1080 × 1920 px (9:16 vertical — TikTok/Reels/Shorts native)
  • Video region: filled with PURE BLACK (#000000)
    — FFmpeg keys out near-pure-black pixels (similarity threshold 0.12)
    — The design team must NOT use dark greys in the brand area;
      any non-black dark colour in the brand card will be left opaque.
  • All other brand elements (logo, text, colours) go outside the black zone.

Alternative: RGBA template with transparent video zone
  — If the design team exports a PNG with an alpha channel (transparent
    video area), the colorkey step is skipped and alpha is used directly.
  — Both modes are supported automatically.

Config (stored in output/store/overlay_config.json or .env):
  OVERLAY_ENABLED=true
  OVERLAY_TEMPLATE=overlays/template.png
  OVERLAY_SIMILARITY=0.12   # colorkey threshold (0=exact black only, 1=all)
"""
import os
import json
import logging
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

OVERLAY_DIR   = Path("overlays")
DEFAULT_TPL   = str(OVERLAY_DIR / "template.png")
CONFIG_FILE   = Path("output/store/overlay_config.json")

OVERLAY_DIR.mkdir(exist_ok=True)


# ── Config ─────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cfg(data: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_cfg()
    existing.update(data)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2))


def get_overlay_config() -> dict:
    stored = _load_cfg()
    return {
        "enabled":     stored.get("enabled",     os.getenv("OVERLAY_ENABLED", "true").lower() == "true"),
        "template":    stored.get("template",     os.getenv("OVERLAY_TEMPLATE", DEFAULT_TPL)),
        "similarity":  stored.get("similarity",   float(os.getenv("OVERLAY_SIMILARITY", "0.12"))),
    }


def save_overlay_config(updates: dict):
    _save_cfg(updates)
    logger.info(f"[Overlay] Config updated: {updates}")


def get_active_template() -> str | None:
    cfg = get_overlay_config()
    path = cfg.get("template") or DEFAULT_TPL
    if path and Path(path).exists():
        return path
    if Path(DEFAULT_TPL).exists():
        return DEFAULT_TPL
    return None


def template_exists() -> bool:
    return get_active_template() is not None


def _has_alpha(template_path: str) -> bool:
    """Return True if the template PNG has an alpha channel."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_streams",
             "-print_format", "json", template_path],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                pix_fmt = s.get("pix_fmt", "")
                return "a" in pix_fmt or pix_fmt in ("rgba", "yuva420p", "png")
    except Exception:
        pass
    return False


# ── Core composite ─────────────────────────────────────────────────────────────

def apply_overlay(clip_path: str, clip_id: str) -> str:
    """
    Composite the branded template frame onto the clip.

    The clip (already 1080×1920 from cut_clip) is used as the base.
    The template is overlaid on top with the black video region keyed out.
    The branded elements (header, brand card) remain visible throughout.

    Returns the path to the final clip (same as input — file is replaced in-place).
    """
    cfg = get_overlay_config()

    if not cfg["enabled"]:
        logger.info(f"[Overlay] Disabled — skipping clip {clip_id}")
        return clip_path

    template_path = get_active_template()
    if not template_path:
        logger.warning(f"[Overlay] No template found — skipping clip {clip_id}")
        return clip_path

    logger.info(f"[Overlay] Compositing brand frame onto clip {clip_id}")

    with tempfile.TemporaryDirectory() as tmp:
        output_path = str(Path(tmp) / "branded.mp4")

        if _has_alpha(template_path):
            _composite_alpha(clip_path, template_path, output_path)
        else:
            _composite_colorkey(clip_path, template_path, output_path, cfg["similarity"])

        # Replace original clip
        shutil.move(output_path, clip_path)

    logger.info(f"[Overlay] Brand frame applied to clip {clip_id}")
    return clip_path


def _composite_colorkey(
    clip_path: str,
    template_path: str,
    output_path: str,
    similarity: float,
) -> None:
    """
    Overlay the template onto the clip, keying out pure-black pixels.

    Filter pipeline:
      [clip]  ─────────────────────────────────┐
                                                ▼ overlay → output
      [tpl] → colorkey(black) → [tpl_keyed] ───┘

    The clip fills the full 1080×1920 frame.
    The template sits on top; black pixels become transparent, showing
    the video underneath. All brand elements stay opaque on top.
    """
    filter_complex = (
        f"[1:v]colorkey=color=0x000000:similarity={similarity:.3f}:blend=0[keyed];"
        f"[0:v][keyed]overlay=0:0[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-i", template_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"[Overlay] Colorkey composite failed for {clip_path}:\n{result.stderr[-600:]}"
        )


def _composite_alpha(
    clip_path: str,
    template_path: str,
    output_path: str,
) -> None:
    """
    Overlay the template (RGBA) directly onto the clip.
    Transparent pixels in the template let the video show through.
    """
    filter_complex = (
        "[0:v][1:v]overlay=0:0[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-i", template_path,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"[Overlay] Alpha composite failed for {clip_path}:\n{result.stderr[-600:]}"
        )


# ── Template management ────────────────────────────────────────────────────────

def save_template(file_bytes: bytes, filename: str) -> str:
    """
    Save a new template and archive the current one.
    Returns path to the new active template.
    """
    OVERLAY_DIR.mkdir(exist_ok=True)

    # Archive current template
    if Path(DEFAULT_TPL).exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = OVERLAY_DIR / f"template_backup_{stamp}.png"
        Path(DEFAULT_TPL).rename(archive)
        logger.info(f"[Overlay] Archived previous template → {archive.name}")

    Path(DEFAULT_TPL).write_bytes(file_bytes)
    save_overlay_config({"template": DEFAULT_TPL})
    logger.info(f"[Overlay] New template saved: {filename} ({len(file_bytes)/1024:.1f} KB)")
    return DEFAULT_TPL


def delete_template():
    t = get_active_template()
    if t and Path(t).exists():
        Path(t).unlink()
    save_overlay_config({"template": None})
    logger.info("[Overlay] Template deleted.")


def list_archived_templates() -> list[dict]:
    archives = []
    for f in OVERLAY_DIR.glob("template_backup_*.png"):
        archives.append({
            "filename": f.name,
            "path": str(f),
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created_at": f.stat().st_mtime,
        })
    return sorted(archives, key=lambda x: x["created_at"], reverse=True)


def restore_template(filename: str) -> str:
    archive = OVERLAY_DIR / filename
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {filename}")
    if Path(DEFAULT_TPL).exists():
        Path(DEFAULT_TPL).unlink()
    shutil.copy(str(archive), DEFAULT_TPL)
    save_overlay_config({"template": DEFAULT_TPL})
    logger.info(f"[Overlay] Restored template from: {filename}")
    return DEFAULT_TPL
