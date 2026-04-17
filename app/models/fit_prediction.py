from __future__ import annotations

from typing import List, Optional

from sqlalchemy import Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FitPrediction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fit_predictions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    garment_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("garment_assets.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    size_label: Mapped[str] = mapped_column(String(32), nullable=False)
    fit_result: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    reason_json: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    user: Mapped["User"] = relationship(back_populates="fit_predictions")
    brand: Mapped["Brand"] = relationship(back_populates="fit_predictions")
    category: Mapped["Category"] = relationship(back_populates="fit_predictions")

