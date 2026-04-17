from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BodyProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "body_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    sex_fit: Mapped[str] = mapped_column(String(32), default="unisex", nullable=False)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    chest_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    waist_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hips_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inseam_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    shoulder_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_preference: Mapped[str] = mapped_column(String(32), default="regular", nullable=False)

    user: Mapped["User"] = relationship(back_populates="body_profile")

