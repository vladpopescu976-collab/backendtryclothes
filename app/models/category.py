from __future__ import annotations

from typing import List

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    size_charts: Mapped[List["BrandSizeChart"]] = relationship(back_populates="category")
    garment_assets: Mapped[List["GarmentAsset"]] = relationship(back_populates="category")
    fit_predictions: Mapped[List["FitPrediction"]] = relationship(back_populates="category")

