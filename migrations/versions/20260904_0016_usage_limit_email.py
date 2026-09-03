"""add usage limit email delivery marker

Revision ID: 20260904_0016
Revises: 20260823_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0016"
down_revision = "20260823_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("usage_limit_email_sent_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("usage_limit_email_sent_at")
