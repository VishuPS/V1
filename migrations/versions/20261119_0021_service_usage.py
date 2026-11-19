"""Separate account-wide monthly quotas for barcode tools."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0021"
down_revision = "20261104_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_scope", sa.String(48), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("units", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_scope", "category", "period_start", name="uq_service_usage_scope_category_period"),
    )


def downgrade():
    op.drop_table("service_usage")
