from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TryOnJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tryon_jobs"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    person_image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    upper_garment_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("garment_assets.id", ondelete="SET NULL"), nullable=True
    )
    lower_garment_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("garment_assets.id", ondelete="SET NULL"), nullable=True
    )
    result_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="tryon_jobs")

