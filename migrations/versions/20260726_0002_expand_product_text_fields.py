"""Use unbounded text for externally supplied product fields.

Revision ID: 20260726_0002
Revises: 20260725_0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0002"
down_revision: str | None = "20260725_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=512),
            type_=sa.Text(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "brand",
            existing_type=sa.String(length=512),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "quantity",
            existing_type=sa.String(length=128),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "image_url",
            existing_type=sa.String(length=2048),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # PostgreSQL will reject this downgrade if existing values exceed the old
    # limits; operators must clean those rows explicitly rather than lose data.
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "image_url",
            existing_type=sa.Text(),
            type_=sa.String(length=2048),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "quantity",
            existing_type=sa.Text(),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "brand",
            existing_type=sa.Text(),
            type_=sa.String(length=512),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "name",
            existing_type=sa.Text(),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
