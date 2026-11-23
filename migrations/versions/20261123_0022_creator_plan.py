"""Generation-only Creator subscription."""
from alembic import op

revision = "20261123_0022"
down_revision = "20261119_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO subscription_plans
          (code,name,monthly_lookups,requests_per_minute,price_cents,currency,active,created_at)
        VALUES ('CREATOR','Creator',0,120,499,'USD',true,CURRENT_TIMESTAMP)
        ON CONFLICT (code) DO NOTHING
    """)


def downgrade():
    op.execute("DELETE FROM subscription_plans WHERE code='CREATOR'")
