"""Idempotent SKU/bulk quota reservations. No uploaded file contents retained."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0027"
down_revision="20261125_0026"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("workspace_receipts",sa.Column("id",sa.String(36),primary_key=True),sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),sa.Column("request_id",sa.String(64),nullable=False),sa.Column("operation",sa.String(20),nullable=False),sa.Column("digest",sa.String(64),nullable=False),sa.Column("result",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("user_id","request_id",name="uq_workspace_receipt"))
    op.create_index("ix_receipt_created","workspace_receipts",["created_at"])


def downgrade():
    op.drop_table("workspace_receipts")
