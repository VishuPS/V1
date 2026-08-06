"""add verified email registration

Revision ID: 20260803_0004
Revises: 20260728_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "20260803_0004"
down_revision = "20260728_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("organization", sa.String(200)),
        sa.Column("use_case", sa.Text()),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("api_client_id", sa.String(36), sa.ForeignKey("api_clients.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_registration_requests_email", "registration_requests", ["email"])
    op.create_index("ix_registration_requests_token_hash", "registration_requests", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_registration_requests_token_hash", table_name="registration_requests")
    op.drop_index("ix_registration_requests_email", table_name="registration_requests")
    op.drop_table("registration_requests")
