from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.brand import Brand
from app.models.category import Category
from app.schemas.common import FitPredictionRequest, FitPredictionResponse
from app.services.auth import get_current_user
from app.services.fit import predict_fit

router = APIRouter()


@router.post("/predict", response_model=FitPredictionResponse)
def fit_predict(
    payload: FitPredictionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> FitPredictionResponse:
    brand = db.query(Brand).filter(Brand.id == payload.brand_id, Brand.active.is_(True)).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found.")

    category = db.query(Category).filter(Category.code == payload.category_code).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")

    try:
        prediction, chart = predict_fit(
            db=db,
            user=current_user,
            brand=brand,
            category=category,
            size_label=payload.size_label,
            garment_asset_id=payload.garment_asset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FitPredictionResponse(
        fit_result=prediction.fit_result,
        confidence_score=prediction.confidence_score,
        reasons=prediction.reason_json,
        brand=brand,
        category=category,
        size_label=prediction.size_label,
        chart_source_type=chart.source_type,
    )

