"""add annual subscription billing interval

Revision ID: 20260812_0011
Revises: 20260812_0010
"""

import sqlalchemy as sa
from alembic import op


revision = "20260812_0011"
down_revision = "20260812_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("billing_interval", sa.String(16), nullable=False, server_default="month"),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "billing_interval")
