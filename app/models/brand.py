from __future__ import annotations

from typing import Any, List

from sqlalchemy import Boolean, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    aliases_json: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    size_charts: Mapped[List["BrandSizeChart"]] = relationship(back_populates="brand")
    garment_assets: Mapped[List["GarmentAsset"]] = relationship(back_populates="brand")
    fit_predictions: Mapped[List["FitPrediction"]] = relationship(back_populates="brand")

