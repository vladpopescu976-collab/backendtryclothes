from __future__ import annotations

import base64
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.body_profile import BodyProfile
from app.schemas.common import BodyProfileRead, BodyProfileUpdate, UserRead
from app.services.auth import get_current_user

router = APIRouter()
_MAX_AVATAR_UPLOAD_BYTES = 12 * 1024 * 1024
_AVATAR_DIMENSION = 512


@router.get("/me", response_model=UserRead)
def get_me(current_user=Depends(get_current_user)) -> UserRead:
    return current_user


@router.put("/me/avatar", response_model=UserRead)
async def update_me_avatar(
    avatar_image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> UserRead:
    raw_bytes = await avatar_image.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Profile photo is required.")
    if len(raw_bytes) > _MAX_AVATAR_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Profile photo is too large.")

    try:
        normalized = _prepare_avatar_image(raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current_user.avatar_image_url = _jpeg_data_url(normalized)
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.delete("/me/avatar", response_model=UserRead)
def delete_me_avatar(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> UserRead:
    current_user.avatar_image_url = None
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/body-profile", response_model=BodyProfileRead)
def upsert_body_profile(
    payload: BodyProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BodyProfileRead:
    profile = db.query(BodyProfile).filter(BodyProfile.user_id == current_user.id).first()
    if not profile:
        profile = BodyProfile(user_id=current_user.id)
        db.add(profile)

    for field_name, value in payload.model_dump().items():
        setattr(profile, field_name, value)

    db.commit()
    db.refresh(profile)
    return profile


def _prepare_avatar_image(raw_bytes: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(raw_bytes)) as uploaded:
            normalized = ImageOps.exif_transpose(uploaded)
            if normalized.mode not in ("RGB", "L"):
                normalized = normalized.convert("RGB")
            elif normalized.mode == "L":
                normalized = normalized.convert("RGB")

            width, height = normalized.size
            if width <= 0 or height <= 0:
                raise ValueError("Invalid profile photo.")

            edge = min(width, height)
            left = (width - edge) / 2
            top = (height - edge) / 2
            cropped = normalized.crop((left, top, left + edge, top + edge))
            return cropped.resize((_AVATAR_DIMENSION, _AVATAR_DIMENSION), Image.Resampling.LANCZOS)
    except UnidentifiedImageError as exc:
        raise ValueError("Unsupported profile photo format.") from exc


def _jpeg_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=96, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
