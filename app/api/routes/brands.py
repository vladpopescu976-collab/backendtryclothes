from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.brand import Brand
from app.models.brand_size_chart import BrandSizeChart
from app.models.category import Category
from app.schemas.common import BrandDetectResponse, BrandRead, SizeChartRead
from app.services.auth import get_current_user
from app.services.brand_detection import detect_brand_candidates

router = APIRouter()


@router.get("", response_model=list[BrandRead])
def list_brands(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[BrandRead]:
    query = db.query(Brand).filter(Brand.active.is_(True))
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(Brand.name.ilike(search_term))
    return query.order_by(Brand.name.asc()).all()


@router.get("/{brand_id}/size-chart", response_model=SizeChartRead)
def get_size_chart(
    brand_id: str,
    category_code: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SizeChartRead:
    category = db.query(Category).filter(Category.code == category_code).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")

    chart = (
        db.query(BrandSizeChart)
        .filter(BrandSizeChart.brand_id == brand_id, BrandSizeChart.category_id == category.id)
        .first()
    )
    if not chart:
        raise HTTPException(status_code=404, detail="Size chart not found.")
    return chart


@router.post("/detect", response_model=BrandDetectResponse)
async def detect_brand(
    image: UploadFile = File(...),
    brand_hint_text: Optional[str] = Form(None),
    category_hint_text: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> BrandDetectResponse:
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        await image.close()
        raise HTTPException(status_code=415, detail="Unsupported image type.")

    candidate_brands, category_code, normalized_text = detect_brand_candidates(
        db=db,
        filename=image.filename or "",
        brand_hint_text=brand_hint_text,
        category_hint_text=category_hint_text,
    )
    response = BrandDetectResponse(
        original_filename=image.filename or "",
        candidate_brands=candidate_brands,
        candidate_category_code=category_code,
        normalized_text=normalized_text,
    )
    await image.close()
    return response
