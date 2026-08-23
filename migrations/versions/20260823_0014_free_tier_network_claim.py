"""add privacy-preserving free-tier network claim

Revision ID: 20260823_0014
Revises: 20260819_0013
"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_0014"
down_revision = "20260819_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("free_tier_registration_ip_hash", sa.String(64))
        )
        batch.create_unique_constraint(
            "uq_users_free_tier_registration_ip_hash",
            ["free_tier_registration_ip_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint(
            "uq_users_free_tier_registration_ip_hash", type_="unique"
        )
        batch.drop_column("free_tier_registration_ip_hash")
