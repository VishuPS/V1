"""Add API clients, hashed keys, and monthly aggregate usage.

Revision ID: 20260728_0003
Revises: 20260726_0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260728_0003"
down_revision: str | None = "20260726_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("identifier", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_clients_identifier", "api_clients", ["identifier"], unique=True)

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["api_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_client_id", "api_keys", ["client_id"])
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"], unique=True)

    op.create_table(
        "monthly_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("lookup_count", sa.BigInteger(), nullable=False),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "api_key_id", "period_start", name="uq_monthly_usage_key_period"
        ),
    )
    op.create_index("ix_monthly_usage_api_key_id", "monthly_usage", ["api_key_id"])


def downgrade() -> None:
    op.drop_index("ix_monthly_usage_api_key_id", table_name="monthly_usage")
    op.drop_table("monthly_usage")
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_client_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_api_clients_identifier", table_name="api_clients")
    op.drop_table("api_clients")
