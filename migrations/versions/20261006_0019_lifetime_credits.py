"""Lifetime free-result allowance; carry forward recorded metering."""
from alembic import op
import sqlalchemy as sa
revision = "20261006_0019"
down_revision = "20261001_0018"
branch_labels = None
depends_on = None
def upgrade():
    for table in ("api_clients", "subscriptions"):
        op.add_column(table, sa.Column("free_credits_used", sa.BigInteger(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("free_credits_started_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text("UPDATE " + table + " SET free_credits_started_at = CURRENT_TIMESTAMP"))
    # Preserve recorded usage across all keys and periods, including legacy attempts.
    op.execute(sa.text("""
        UPDATE api_clients SET free_credits_used = COALESCE((
            SELECT SUM(mu.lookup_count) FROM monthly_usage mu
            JOIN api_keys k ON k.id = mu.api_key_id
            WHERE k.client_id = api_clients.id
        ), 0)
    """))
    op.execute(sa.text("""
        UPDATE subscriptions SET free_credits_used = CASE
            WHEN monthly_calls_used > COALESCE((
                SELECT SUM(c.free_credits_used) FROM api_clients c
                WHERE c.owner_user_id = subscriptions.user_id
            ), 0) THEN monthly_calls_used
            ELSE COALESCE((SELECT SUM(c.free_credits_used) FROM api_clients c
                           WHERE c.owner_user_id = subscriptions.user_id), 0)
        END WHERE plan_code = 'FREE'
    """))

def downgrade():
    for table in ("subscriptions", "api_clients"):
        op.drop_column(table, "free_credits_started_at")
        op.drop_column(table, "free_credits_used")
