"""Initial canonical product schema.

Revision ID: 20260725_0001
Revises:
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260725_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("barcode", sa.String(length=14), nullable=False),
        sa.Column("barcode_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("brand", sa.String(length=512), nullable=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.String(length=128), nullable=True),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("ingredients", sa.Text(), nullable=True),
        sa.Column("allergens", sa.JSON(), nullable=False),
        sa.Column("nutrition", sa.JSON(), nullable=False),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("barcode", name="pk_products"),
    )
    op.create_index("ix_products_barcode_type", "products", ["barcode_type"])
    op.create_index(
        "ix_products_source_identity",
        "products",
        ["source", "source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_products_source_identity", table_name="products")
    op.drop_index("ix_products_barcode_type", table_name="products")
    op.drop_table("products")

