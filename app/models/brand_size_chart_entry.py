from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrandSizeChartEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brand_size_chart_entries"

    chart_id: Mapped[str] = mapped_column(ForeignKey("brand_size_charts.id", ondelete="CASCADE"), nullable=False)
    size_label: Mapped[str] = mapped_column(String(32), nullable=False)
    chest_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    chest_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    waist_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    waist_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hips_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hips_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inseam_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inseam_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    chart: Mapped["BrandSizeChart"] = relationship(back_populates="entries")

