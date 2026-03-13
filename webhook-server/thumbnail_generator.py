"""Phase 4: Thumbnail generation utilities.

Image resize and video poster frame extraction.
Main thumbnail logic is in asset_uploader.py; this module provides
standalone utilities for batch operations and video poster frames.
"""

import io
import logging
import subprocess
import tempfile
import os

from PIL import Image

logger = logging.getLogger(__name__)

THUMBNAIL_WIDTH = 400


def generate_image_thumbnail(image_data: bytes, width: int = THUMBNAIL_WIDTH):
    """Resize image data to specified width, maintaining aspect ratio."""
    try:
        img = Image.open(io.BytesIO(image_data))
        ratio = width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((width, new_height), Image.LANCZOS)

        buf = io.BytesIO()
        fmt = "JPEG" if img.mode == "RGB" else "PNG"
        img.save(buf, format=fmt, quality=80)
        return buf.getvalue()
    except Exception as e:
        logger.error("Image thumbnail failed: %s", e)
        return None


def extract_video_poster(video_data: bytes, timestamp: str = "00:00:01"):
    """Extract a poster frame from video data using ffmpeg.

    Returns JPEG image bytes, or None if ffmpeg is not available.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
            tmp_vid.write(video_data)
            tmp_vid_path = tmp_vid.name

        tmp_img_path = tmp_vid_path.replace(".mp4", "_poster.jpg")

        result = subprocess.run(
            [
                "ffmpeg", "-i", tmp_vid_path,
                "-ss", timestamp,
                "-vframes", "1",
                "-q:v", "3",
                tmp_img_path,
            ],
            capture_output=True,
            timeout=30,
        )

        if result.returncode == 0 and os.path.exists(tmp_img_path):
            with open(tmp_img_path, "rb") as f:
                poster_data = f.read()
            # Resize to thumbnail width
            return generate_image_thumbnail(poster_data) or poster_data
        else:
            logger.warning("ffmpeg poster extraction failed: %s", result.stderr.decode())
            return None

    except FileNotFoundError:
        logger.warning("ffmpeg not found — video poster extraction unavailable")
        return None
    except Exception as e:
        logger.error("Video poster extraction failed: %s", e)
        return None
    finally:
        for p in [tmp_vid_path, tmp_img_path]:
            try:
                os.unlink(p)
            except OSError:
                pass
