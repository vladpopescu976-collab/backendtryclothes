from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    first_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verification_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verification_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_reset_token_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_reset_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    body_profile: Mapped[Optional["BodyProfile"]] = relationship(back_populates="user", uselist=False)
    garment_assets: Mapped[List["GarmentAsset"]] = relationship(back_populates="user")
    fit_predictions: Mapped[List["FitPrediction"]] = relationship(back_populates="user")
    tryon_jobs: Mapped[List["TryOnJob"]] = relationship(back_populates="user")
