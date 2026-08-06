"""add users, JWT sessions, API key ownership, and subscriptions

Revision ID: 20260805_0005
Revises: 20260803_0004
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


revision = "20260805_0005"
down_revision = "20260803_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    created_at = datetime.now(timezone.utc)
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("organization", sa.String(200)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    plans = op.create_table(
        "subscription_plans",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("monthly_lookups", sa.BigInteger(), nullable=False),
        sa.Column("requests_per_minute", sa.BigInteger(), nullable=False),
        sa.Column("price_cents", sa.BigInteger()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.bulk_insert(
        plans,
        [
            {"code": "FREE", "name": "Free", "monthly_lookups": 500, "requests_per_minute": 30, "price_cents": 0, "currency": "EUR", "active": True, "created_at": created_at},
            {"code": "STARTER", "name": "Starter", "monthly_lookups": 10000, "requests_per_minute": 300, "price_cents": None, "currency": "EUR", "active": False, "created_at": created_at},
            {"code": "PRO", "name": "Pro", "monthly_lookups": 100000, "requests_per_minute": 1200, "price_cents": None, "currency": "EUR", "active": False, "created_at": created_at},
        ],
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("plan_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32)),
        sa.Column("provider_customer_id", sa.String(255)),
        sa.Column("provider_subscription_id", sa.String(255)),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_code"], ["subscription_plans.code"]),
        sa.UniqueConstraint("provider_subscription_id", name="uq_subscriptions_provider_subscription_id"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_plan_code", "subscriptions", ["plan_code"])
    op.create_index("ix_subscriptions_provider_customer_id", "subscriptions", ["provider_customer_id"])

    with op.batch_alter_table("api_clients") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(36)))
        batch.create_foreign_key("fk_api_clients_owner_user_id", "users", ["owner_user_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_api_clients_owner_user_id", ["owner_user_id"])
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(36)))
        batch.create_foreign_key("fk_api_keys_owner_user_id", "users", ["owner_user_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_api_keys_owner_user_id", ["owner_user_id"])
    with op.batch_alter_table("registration_requests") as batch:
        batch.add_column(sa.Column("password_hash", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("registration_requests") as batch:
        batch.drop_column("password_hash")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_index("ix_api_keys_owner_user_id")
        batch.drop_constraint("fk_api_keys_owner_user_id", type_="foreignkey")
        batch.drop_column("owner_user_id")
    with op.batch_alter_table("api_clients") as batch:
        batch.drop_index("ix_api_clients_owner_user_id")
        batch.drop_constraint("fk_api_clients_owner_user_id", type_="foreignkey")
        batch.drop_column("owner_user_id")
    op.drop_index("ix_subscriptions_provider_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_code", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("subscription_plans")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
