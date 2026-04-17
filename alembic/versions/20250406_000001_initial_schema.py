"""Initial schema for VTON Fit Soft.

Revision ID: 20250406_000001
Revises:
Create Date: 2026-04-06 00:00:01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20250406_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_brands"),
        sa.UniqueConstraint("name", name="uq_brands_name"),
        sa.UniqueConstraint("slug", name="uq_brands_slug"),
    )
    op.create_index("ix_brands_slug", "brands", ["slug"], unique=False)

    op.create_table(
        "categories",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.UniqueConstraint("code", name="uq_categories_code"),
    )
    op.create_index("ix_categories_code", "categories", ["code"], unique=False)

    op.create_table(
        "users",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "body_profiles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("sex_fit", sa.String(length=32), nullable=False, server_default="unisex"),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("chest_cm", sa.Float(), nullable=True),
        sa.Column("waist_cm", sa.Float(), nullable=True),
        sa.Column("hips_cm", sa.Float(), nullable=True),
        sa.Column("inseam_cm", sa.Float(), nullable=True),
        sa.Column("shoulder_cm", sa.Float(), nullable=True),
        sa.Column("fit_preference", sa.String(length=32), nullable=False, server_default="regular"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_body_profiles_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_body_profiles"),
        sa.UniqueConstraint("user_id", name="uq_body_profiles_user_id"),
    )

    op.create_table(
        "brand_size_charts",
        sa.Column("brand_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.Column("gender_fit", sa.String(length=32), nullable=False, server_default="unisex"),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="seed_estimate"),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=False, server_default="v1"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], name="fk_brand_size_charts_brand_id_brands", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_brand_size_charts_category_id_categories", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_size_charts"),
    )

    op.create_table(
        "brand_size_chart_entries",
        sa.Column("chart_id", sa.String(), nullable=False),
        sa.Column("size_label", sa.String(length=32), nullable=False),
        sa.Column("chest_min", sa.Float(), nullable=True),
        sa.Column("chest_max", sa.Float(), nullable=True),
        sa.Column("waist_min", sa.Float(), nullable=True),
        sa.Column("waist_max", sa.Float(), nullable=True),
        sa.Column("hips_min", sa.Float(), nullable=True),
        sa.Column("hips_max", sa.Float(), nullable=True),
        sa.Column("inseam_min", sa.Float(), nullable=True),
        sa.Column("inseam_max", sa.Float(), nullable=True),
        sa.Column("fit_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chart_id"], ["brand_size_charts.id"], name="fk_brand_size_chart_entries_chart_id_brand_size_charts", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brand_size_chart_entries"),
    )

    op.create_table(
        "garment_assets",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=False),
        sa.Column("brand_id", sa.String(), nullable=True),
        sa.Column("category_id", sa.String(), nullable=True),
        sa.Column("detected_brand_name", sa.String(length=120), nullable=True),
        sa.Column("detected_text", sa.Text(), nullable=True),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], name="fk_garment_assets_brand_id_brands", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_garment_assets_category_id_categories", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_garment_assets_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_garment_assets"),
    )

    op.create_table(
        "fit_predictions",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("garment_asset_id", sa.String(), nullable=True),
        sa.Column("brand_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.Column("size_label", sa.String(length=32), nullable=False),
        sa.Column("fit_result", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], name="fk_fit_predictions_brand_id_brands", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name="fk_fit_predictions_category_id_categories", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["garment_asset_id"], ["garment_assets.id"], name="fk_fit_predictions_garment_asset_id_garment_assets", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_fit_predictions_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_fit_predictions"),
    )

    op.create_table(
        "tryon_jobs",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("person_image_url", sa.String(length=500), nullable=False),
        sa.Column("upper_garment_asset_id", sa.String(), nullable=True),
        sa.Column("lower_garment_asset_id", sa.String(), nullable=True),
        sa.Column("result_image_url", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lower_garment_asset_id"], ["garment_assets.id"], name="fk_tryon_jobs_lower_garment_asset_id_garment_assets", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["upper_garment_asset_id"], ["garment_assets.id"], name="fk_tryon_jobs_upper_garment_asset_id_garment_assets", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_tryon_jobs_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_tryon_jobs"),
    )


def downgrade() -> None:
    op.drop_table("tryon_jobs")
    op.drop_table("fit_predictions")
    op.drop_table("garment_assets")
    op.drop_table("brand_size_chart_entries")
    op.drop_table("brand_size_charts")
    op.drop_table("body_profiles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_categories_code", table_name="categories")
    op.drop_table("categories")
    op.drop_index("ix_brands_slug", table_name="brands")
    op.drop_table("brands")

