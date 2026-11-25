"""Versioned workspace entitlements and verified public statistics."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0024"
down_revision="20261124_0023"
branch_labels=None
depends_on=None


def upgrade():
    op.add_column("users",sa.Column("workspace_policy",sa.String(24),nullable=False,server_default="legacy"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("workspace_policy",existing_type=sa.String(24),server_default="platform_v1")
    op.create_table("platform_statistics",sa.Column("key",sa.String(64),primary_key=True),
        sa.Column("value",sa.BigInteger(),nullable=False),sa.Column("verified_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("method",sa.String(32),nullable=False))


def downgrade():
    op.drop_table("platform_statistics")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("workspace_policy")
