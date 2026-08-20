"""Add fallback provider state and recovery analytics.

Revision ID: 20260819_0013
Revises: 20260813_0012
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260819_0013"
down_revision: str | None = "20260813_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lookup_analytics", sa.Column("local_found", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("lookup_analytics", sa.Column("fallback_attempted", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("lookup_analytics", sa.Column("providers_attempted", sa.JSON(), server_default="[]", nullable=False))
    op.add_column("lookup_analytics", sa.Column("provider_found", sa.String(length=64), nullable=True))
    op.add_column("lookup_analytics", sa.Column("resolution_source", sa.String(length=64), nullable=True))
    op.add_column("lookup_analytics", sa.Column("resolution_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "fallback_provider_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_gtin", sa.String(length=14), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_after_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_gtin", "provider", name="uq_fallback_state_gtin_provider"),
    )
    op.create_index("ix_fallback_provider_states_canonical_gtin", "fallback_provider_states", ["canonical_gtin"])
    op.create_index("ix_fallback_state_expires", "fallback_provider_states", ["provider", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_fallback_state_expires", table_name="fallback_provider_states")
    op.drop_index("ix_fallback_provider_states_canonical_gtin", table_name="fallback_provider_states")
    op.drop_table("fallback_provider_states")
    for column in ("resolution_timestamp", "resolution_source", "provider_found", "providers_attempted", "fallback_attempted", "local_found"):
        op.drop_column("lookup_analytics", column)
