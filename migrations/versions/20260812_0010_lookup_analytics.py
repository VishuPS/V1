"""add privacy-conscious lookup analytics

Revision ID: 20260812_0010
Revises: 20260806_0009
"""

import sqlalchemy as sa
from alembic import op


revision = "20260812_0010"
down_revision = "20260806_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lookup_analytics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("canonical_gtin", sa.String(14), nullable=False),
        sa.Column("barcode_type", sa.String(16), nullable=False),
        sa.Column("endpoint_type", sa.String(16), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("plan_code", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lookup_analytics_request_id", "lookup_analytics", ["request_id"])
    op.create_index("ix_lookup_analytics_api_key_id", "lookup_analytics", ["api_key_id"])
    op.create_index("ix_lookup_analytics_owner_user_id", "lookup_analytics", ["owner_user_id"])
    op.create_index("ix_lookup_analytics_occurred_at", "lookup_analytics", ["occurred_at"])
    op.create_index("ix_lookup_analytics_occurred_found", "lookup_analytics", ["occurred_at", "found"])
    op.create_index("ix_lookup_analytics_gtin_found", "lookup_analytics", ["canonical_gtin", "found"])


def downgrade() -> None:
    op.drop_table("lookup_analytics")
