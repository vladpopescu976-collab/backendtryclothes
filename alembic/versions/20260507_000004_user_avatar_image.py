"""Add avatar image storage to users.

Revision ID: 20260507_000004
Revises: 20260410_000003
Create Date: 2026-05-07 00:00:04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260507_000004"
down_revision = "20260410_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_image_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_image_url")
