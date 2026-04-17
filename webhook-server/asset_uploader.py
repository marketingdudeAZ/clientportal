"""Phase 4: Asset upload to HubSpot Files API + HubDB row creation."""

import io
import logging
import time

import requests
from PIL import Image

from config import (
    HUBSPOT_API_KEY,
    HUBDB_ASSET_TABLE_ID,
    ALLOWED_IMAGE_TYPES,
    ALLOWED_VIDEO_TYPES,
    ALLOWED_DOC_TYPES,
    MAX_UPLOAD_SIZE_MB,
)

logger = logging.getLogger(__name__)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
ALLOWED_EXTENSIONS = ALLOWED_IMAGE_TYPES + ALLOWED_VIDEO_TYPES + ALLOWED_DOC_TYPES
THUMBNAIL_WIDTH = 400

# ── Compression constants (spec A.1) ──────────────────────────────────────────
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024   # 2 MB
MAX_DIMENSION = 2000                      # px on longest edge
JPEG_QUALITY_START = 85
JPEG_QUALITY_FLOOR = 70
THUMBNAIL_QUALITY = 60


def compress_image(file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """Resize and compress a raster image per spec A.2.

    Returns (compressed_bytes, output_filename).
    Non-image files and SVG/PDF are returned unchanged.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Pass-through for non-raster types
    if ext in ("svg", "pdf", "ai", "eps"):
        return file_bytes, filename
    if ext not in ALLOWED_IMAGE_TYPES:
        return file_bytes, filename

    # HEIC/WEBP: attempt conversion via pillow-heif / built-in WEBP
    if ext in ("heic", "heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            logger.warning("pillow-heif not installed — HEIC upload rejected")
            raise ValueError("HEIC uploads require pillow-heif. Please convert to JPG first.")

    try:
        img = Image.open(io.BytesIO(file_bytes))
    except Exception as e:
        logger.error("Image open failed for %s: %s", filename, e)
        return file_bytes, filename

    has_alpha = img.mode in ("RGBA", "LA", "PA")

    # Step 1: Resize if either dimension > MAX_DIMENSION
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        logger.debug("Resized %s to %s", filename, img.size)

    # Step 2: Determine output format
    if ext == "png" and has_alpha:
        output_format = "PNG"
    else:
        # Convert PNG without alpha, HEIC, WEBP, etc. → JPEG
        output_format = "JPEG"
        if img.mode != "RGB":
            img = img.convert("RGB")
        if ext != "jpg" and ext != "jpeg":
            filename = filename.rsplit(".", 1)[0] + ".jpg"

    # Step 3: Compress
    if output_format == "JPEG":
        quality = JPEG_QUALITY_START
        buf = io.BytesIO()
        while quality >= JPEG_QUALITY_FLOOR:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, progressive=True)
            if buf.tell() <= MAX_COMPRESSED_BYTES:
                break
            quality -= 5
        logger.debug("Compressed %s → %d bytes at quality %d", filename, buf.tell(), quality)
        return buf.getvalue(), filename
    else:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        logger.debug("Compressed PNG %s → %d bytes", filename, buf.tell())
        return buf.getvalue(), filename


def process_asset_upload(
    property_uuid: str,
    files: list,
    metadata: list[dict] | None = None,
    # Legacy batch-level fields (backward compatible if metadata is None)
    category: str = "",
    subcategory: str = "",
    description: str = "",
):
    """Upload files to HubSpot Files API, create HubDB rows, publish table.

    Per-file metadata mode (preferred):
        metadata = [{"category": ..., "subcategory": ..., "description": ...}, ...]
        where metadata[i] corresponds to files[i].

    Legacy batch mode (fallback):
        Pass category/subcategory/description as kwargs — applied to all files.
    """
    results = []
    metadata = metadata or []

    for i, file_storage in enumerate(files):
        # Resolve per-file metadata
        m = metadata[i] if i < len(metadata) else {}
        f_category    = m.get("category")    or category
        f_subcategory = m.get("subcategory") or subcategory
        f_description = m.get("description") or description

        filename = file_storage.filename
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ALLOWED_EXTENSIONS:
            logger.warning("Skipping unsupported file type: %s", filename)
            continue

        file_data = file_storage.read()
        if len(file_data) > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            logger.warning("Skipping oversized file: %s (%d bytes)", filename, len(file_data))
            continue

        # Determine asset name: prefer description if given, otherwise derive from filename
        if f_description:
            asset_name = f_description[:100]
        else:
            asset_name = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()

        # Compress raster images before upload (spec A.2)
        if ext in ALLOWED_IMAGE_TYPES:
            try:
                file_data, filename = compress_image(file_data, filename)
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ext
                logger.info("Compressed %s → %d bytes", filename, len(file_data))
            except ValueError as e:
                logger.warning("Skipping %s: %s", filename, e)
                continue

        # Upload to HubSpot Files API
        folder_path = f"/property-assets/{property_uuid}/{(f_category or 'uncategorized').lower().replace(' ', '-')}/{time.strftime('%Y-%m')}"
        file_url = upload_to_files_api(file_data, filename, folder_path)

        if not file_url:
            logger.error("Failed to upload file: %s", filename)
            continue

        # Generate thumbnail for images (using already-compressed data)
        thumbnail_url = None
        if ext in ALLOWED_IMAGE_TYPES:
            thumbnail_url = generate_and_upload_thumbnail(file_data, filename, folder_path)

        # Create HubDB row
        row_data = {
            "property_uuid": property_uuid,
            "file_url": file_url,
            "thumbnail_url": thumbnail_url or "",
            "asset_name": asset_name,
            "category": f_category,
            "subcategory": f_subcategory,
            "status": "live",
            "source": "client_upload",
            "uploaded_by": "client",
            "uploaded_at": int(time.time() * 1000),
            "file_type": ext,
            "file_size_bytes": len(file_data),
            "description": f_description,
        }

        row_id = create_hubdb_row(row_data)
        results.append({
            "filename": filename,
            "file_url": file_url,
            "thumbnail_url": thumbnail_url or "",
            "asset_name": asset_name,
            "category": f_category,
            "subcategory": f_subcategory,
            "row_id": row_id,
        })

    # Publish HubDB table to make new rows visible
    if results:
        publish_hubdb_table()

    return results


def upload_to_files_api(file_data: bytes, filename: str, folder_path: str):
    """Upload a file to HubSpot Files API. Returns the public URL."""
    url = "https://api.hubapi.com/files/v3/files"
    files = {
        "file": (filename, io.BytesIO(file_data)),
    }
    data = {
        "folderPath": folder_path,
        "options": '{"access": "PUBLIC_NOT_INDEXABLE", "overwrite": false}',
    }

    resp = requests.post(url, headers=HEADERS, files=files, data=data)
    if resp.status_code in (200, 201):
        return resp.json().get("url")
    logger.error("Files API upload failed (%d): %s", resp.status_code, resp.text)
    return None


def generate_and_upload_thumbnail(
    file_data: bytes, filename: str, folder_path: str
):
    """Resize image to 400px wide thumbnail and upload. Returns thumbnail URL."""
    try:
        img = Image.open(io.BytesIO(file_data))
        if img.width > THUMBNAIL_WIDTH:
            ratio = THUMBNAIL_WIDTH / img.width
            img = img.resize((THUMBNAIL_WIDTH, int(img.height * ratio)), Image.LANCZOS)

        if img.mode != "RGB":
            img = img.convert("RGB")

        thumb_buffer = io.BytesIO()
        img.save(thumb_buffer, format="JPEG", quality=THUMBNAIL_QUALITY, progressive=True)
        thumb_data = thumb_buffer.getvalue()

        # Ensure thumb filename is .jpg
        base = filename.rsplit(".", 1)[0]
        thumb_name = f"thumb_{base}.jpg"
        return upload_to_files_api(thumb_data, thumb_name, f"{folder_path}/thumbnails")
    except Exception as e:
        logger.error("Thumbnail generation failed for %s: %s", filename, e)
        return None


def create_hubdb_row(values: dict):
    """Create a row in the HubDB asset table."""
    url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/rows"
    resp = requests.post(
        url,
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"values": values},
    )
    if resp.status_code in (200, 201):
        return resp.json().get("id")
    logger.error("HubDB row creation failed (%d): %s", resp.status_code, resp.text)
    return None


def publish_hubdb_table():
    """Publish the HubDB table to make row changes visible."""
    url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_ASSET_TABLE_ID}/draft/publish"
    resp = requests.post(url, headers=HEADERS)
    if resp.status_code not in (200, 201):
        logger.error("HubDB publish failed (%d): %s", resp.status_code, resp.text)
        # Retry once after 30 seconds
        time.sleep(30)
        resp = requests.post(url, headers=HEADERS)
        if resp.status_code not in (200, 201):
            logger.error("HubDB publish retry failed (%d): %s", resp.status_code, resp.text)
