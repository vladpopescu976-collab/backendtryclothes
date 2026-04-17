from __future__ import annotations

from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.category import Category
from app.models.garment_asset import GarmentAsset
from app.models.tryon_job import TryOnJob
from app.models.user import User


async def save_upload_file(upload: UploadFile, destination_dir: Path, prefix: str) -> Path:
    if upload.content_type not in settings.ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {upload.content_type}",
        )

    content = await upload.read()
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit.",
        )

    suffix = Path(upload.filename or "").suffix or ".png"
    file_path = destination_dir / f"{prefix}_{uuid4().hex}{suffix}"
    file_path.write_bytes(content)
    await upload.close()
    return file_path


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
