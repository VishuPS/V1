"""Shared provider throttling and separate infrastructure retry budget."""
from alembic import op
import sqlalchemy as sa
revision = "20261001_0018"
down_revision = "20260929_0017"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("product_recovery", sa.Column("infrastructure_attempts", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table("provider_cooldowns",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(128)))

def downgrade():
    op.drop_table("provider_cooldowns")
    op.drop_column("product_recovery", "infrastructure_attempts")
