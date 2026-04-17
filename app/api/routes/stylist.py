from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import StylistRecommendationRequest, StylistRecommendationResponse
from app.services.auth import get_current_user
from app.services.stylist import build_stylist_recommendation

router = APIRouter()


@router.post("/recommend", response_model=StylistRecommendationResponse)
def stylist_recommend(
    payload: StylistRecommendationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> StylistRecommendationResponse:
    return build_stylist_recommendation(db=db, user=current_user, payload=payload)
