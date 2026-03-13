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


def process_asset_upload(
    property_uuid: str,
    category: str,
    subcategory: str,
    description: str,
    files: list,
):
    """Upload files to HubSpot Files API, create HubDB rows, publish table."""
    results = []

    for file_storage in files:
        filename = file_storage.filename
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext not in ALLOWED_EXTENSIONS:
            logger.warning("Skipping unsupported file type: %s", filename)
            continue

        file_data = file_storage.read()
        if len(file_data) > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            logger.warning("Skipping oversized file: %s (%d bytes)", filename, len(file_data))
            continue

        # Determine asset name from filename
        asset_name = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()

        # Upload to HubSpot Files API
        folder_path = f"/property-assets/{property_uuid}/{category.lower().replace(' ', '-')}/{time.strftime('%Y-%m')}"
        file_url = upload_to_files_api(file_data, filename, folder_path)

        if not file_url:
            logger.error("Failed to upload file: %s", filename)
            continue

        # Generate thumbnail for images
        thumbnail_url = None
        if ext in ALLOWED_IMAGE_TYPES:
            thumbnail_url = generate_and_upload_thumbnail(file_data, filename, folder_path)

        # Create HubDB row
        row_data = {
            "property_uuid": property_uuid,
            "file_url": file_url,
            "thumbnail_url": thumbnail_url or "",
            "asset_name": asset_name,
            "category": category,
            "subcategory": subcategory,
            "status": "live",
            "source": "client_upload",
            "uploaded_by": "client",
            "uploaded_at": int(time.time() * 1000),
            "file_type": ext,
            "file_size_bytes": len(file_data),
            "description": description,
        }

        row_id = create_hubdb_row(row_data)
        results.append({"filename": filename, "file_url": file_url, "row_id": row_id})

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
    """Resize image to thumbnail and upload. Returns thumbnail URL."""
    try:
        img = Image.open(io.BytesIO(file_data))
        ratio = THUMBNAIL_WIDTH / img.width
        new_height = int(img.height * ratio)
        img = img.resize((THUMBNAIL_WIDTH, new_height), Image.LANCZOS)

        thumb_buffer = io.BytesIO()
        img_format = "JPEG" if img.mode == "RGB" else "PNG"
        img.save(thumb_buffer, format=img_format, quality=80)
        thumb_data = thumb_buffer.getvalue()

        thumb_name = f"thumb_{filename}"
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
