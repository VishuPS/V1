"""add admin authorization and daily usage aggregates

Revision ID: 20260806_0008
Revises: 20260805_0007
"""

import sqlalchemy as sa
from alembic import op


revision = "20260806_0008"
down_revision = "20260805_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "daily_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "api_key_id",
            sa.String(36),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lookup_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("api_key_id", "usage_date", name="uq_daily_usage_key_date"),
    )
    op.create_index("ix_daily_usage_api_key_id", "daily_usage", ["api_key_id"])
    op.create_index("ix_daily_usage_usage_date", "daily_usage", ["usage_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_usage_usage_date", table_name="daily_usage")
    op.drop_index("ix_daily_usage_api_key_id", table_name="daily_usage")
    op.drop_table("daily_usage")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_admin")
