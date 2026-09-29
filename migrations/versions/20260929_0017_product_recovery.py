"""Durable missing-product recovery queue."""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0017"
down_revision = "20260904_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_recovery",
        sa.Column("canonical_gtin", sa.String(14), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("provider", sa.String(128)),
        sa.Column("detail", sa.String(256)),
    )
    op.create_index("ix_product_recovery_next_attempt", "product_recovery", ["next_attempt"])


def downgrade():
    op.drop_table("product_recovery")
