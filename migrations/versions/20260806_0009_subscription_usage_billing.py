"""add account usage enforcement and Stripe billing metadata

Revision ID: 20260806_0009
Revises: 20260806_0008
"""

from datetime import datetime, timezone
from calendar import monthrange

import sqlalchemy as sa
from alembic import op

revision = "20260806_0009"
down_revision = "20260806_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    period_end = now.replace(day=monthrange(now.year, now.month)[1], hour=23, minute=59, second=59, microsecond=999999)
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("provider_price_id", sa.String(255)))
        batch.add_column(sa.Column("monthly_call_limit", sa.BigInteger(), nullable=False, server_default="250"))
        batch.add_column(sa.Column("monthly_calls_used", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("usage_period_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
        batch.add_column(sa.Column("usage_period_end", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("usage_warning_sent_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_subscriptions_provider_price_id", ["provider_price_id"])
    op.execute(sa.update(sa.table("subscriptions", sa.column("usage_period_end"))).values(usage_period_end=period_end))
    with op.batch_alter_table("subscriptions") as batch:
        batch.alter_column("usage_period_end", nullable=False)
    op.execute(sa.text("UPDATE subscription_plans SET monthly_lookups=250, price_cents=0, currency='USD', active=true WHERE code='FREE'"))
    op.execute(sa.text("UPDATE subscription_plans SET monthly_lookups=2000, price_cents=999, currency='USD', active=true WHERE code='STARTER'"))
    plans = sa.table("subscription_plans", sa.column("code"), sa.column("name"), sa.column("monthly_lookups"), sa.column("requests_per_minute"), sa.column("price_cents"), sa.column("currency"), sa.column("active"), sa.column("created_at"))
    op.bulk_insert(plans, [{"code":"GROWTH","name":"Growth","monthly_lookups":5000,"requests_per_minute":1200,"price_cents":1999,"currency":"USD","active":True,"created_at":now}])
    op.execute(sa.text("UPDATE subscriptions SET plan_code='GROWTH', monthly_call_limit=5000 WHERE plan_code='PRO'"))
    op.execute(sa.text("UPDATE api_clients SET plan='GROWTH' WHERE plan='PRO'"))
    op.execute(sa.text("UPDATE subscriptions SET monthly_call_limit=CASE plan_code WHEN 'STARTER' THEN 2000 WHEN 'GROWTH' THEN 5000 ELSE 250 END"))
    op.create_table("stripe_webhook_events", sa.Column("event_id", sa.String(255), primary_key=True), sa.Column("event_type", sa.String(128), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("stripe_webhook_events")
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_index("ix_subscriptions_provider_price_id")
        for column in ["usage_warning_sent_at", "usage_period_end", "usage_period_start", "monthly_calls_used", "monthly_call_limit", "provider_price_id"]:
            batch.drop_column(column)
