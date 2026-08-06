"""allow accounts without email verification

Revision ID: 20260805_0006
Revises: 20260805_0005
"""

from alembic import op


revision = "20260805_0006"
down_revision = "20260805_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email_verified_at", nullable=True)


def downgrade() -> None:
    # Existing unverified rows need an explicit policy before reverting.
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email_verified_at", nullable=False)
