from __future__ import annotations

from typing import List, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrandSizeChart(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brand_size_charts"

    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    gender_fit: Mapped[str] = mapped_column(String(32), default="unisex", nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="seed_estimate", nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    brand: Mapped["Brand"] = relationship(back_populates="size_charts")
    category: Mapped["Category"] = relationship(back_populates="size_charts")
    entries: Mapped[List["BrandSizeChartEntry"]] = relationship(back_populates="chart", cascade="all, delete-orphan")

