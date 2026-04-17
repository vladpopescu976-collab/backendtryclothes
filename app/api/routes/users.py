from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.body_profile import BodyProfile
from app.schemas.common import BodyProfileRead, BodyProfileUpdate, UserRead
from app.services.auth import get_current_user

router = APIRouter()


@router.get("/me", response_model=UserRead)
def get_me(current_user=Depends(get_current_user)) -> UserRead:
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

