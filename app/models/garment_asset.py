from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GarmentAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "garment_assets"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    brand_id: Mapped[Optional[str]] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)
    category_id: Mapped[Optional[str]] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    detected_brand_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    detected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detection_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship(back_populates="garment_assets")
    brand: Mapped[Optional["Brand"]] = relationship(back_populates="garment_assets")
    category: Mapped[Optional["Category"]] = relationship(back_populates="garment_assets")

