from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.category import Category
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class SavedUploadInfo:
    path: Path
    upload_bytes: int
    image_width: Optional[int]
    image_height: Optional[int]
    content_type: Optional[str]
    original_filename: Optional[str]
    read_ms: int
    decode_ms: int
    write_ms: int


async def save_upload_file(upload: UploadFile, destination_dir: Path, prefix: str) -> Path:
    return (await save_upload_file_with_metrics(upload, destination_dir, prefix)).path


async def save_upload_file_with_metrics(upload: UploadFile, destination_dir: Path, prefix: str) -> SavedUploadInfo:
    if upload.content_type not in settings.ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {upload.content_type}",
        )

    read_started = time.monotonic()
    content = await upload.read()
    read_ms = int((time.monotonic() - read_started) * 1000)
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit.",
        )

    decode_started = time.monotonic()
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            image_width, image_height = image.size
    except Exception:
        logger.warning("Could not decode uploaded image metadata for %s", upload.filename)
    decode_ms = int((time.monotonic() - decode_started) * 1000)

    suffix = Path(upload.filename or "").suffix or ".png"
    file_path = destination_dir / f"{prefix}_{uuid4().hex}{suffix}"
    write_started = time.monotonic()
    file_path.write_bytes(content)
    write_ms = int((time.monotonic() - write_started) * 1000)
    await upload.close()

    return SavedUploadInfo(
        path=file_path,
        upload_bytes=len(content),
        image_width=image_width,
        image_height=image_height,
        content_type=upload.content_type,
        original_filename=upload.filename,
        read_ms=read_ms,
        decode_ms=decode_ms,
        write_ms=write_ms,
    )


def create_garment_asset(
    db: Session,
    user: User,
    image_path: Path,
    brand_id: Optional[str] = None,
    category_code: Optional[str] = None,
) -> GarmentAsset:
    category = None
    if category_code:
        category = db.query(Category).filter(Category.code == category_code).first()
        if not category:
            category = Category(
                code=category_code,
                name=category_code.replace("-", " ").replace("_", " ").title(),
            )
            db.add(category)
            db.flush()

    asset = GarmentAsset(
        user_id=user.id,
        image_url=str(image_path),
        brand_id=brand_id,
        category_id=category.id if category else None,
    )
    db.add(asset)
    db.flush()
    return asset


def create_tryon_job(
    db: Session,
    user: User,
    person_image_path: Path,
    upper_asset: Optional[GarmentAsset],
    lower_asset: Optional[GarmentAsset],
) -> TryOnJob:
    job = TryOnJob(
        user_id=user.id,
        status="queued",
        person_image_url=str(person_image_path),
        upper_garment_asset_id=upper_asset.id if upper_asset else None,
        lower_garment_asset_id=lower_asset.id if lower_asset else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
