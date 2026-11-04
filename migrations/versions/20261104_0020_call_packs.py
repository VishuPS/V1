"""Non-recurring 30-day call packs; existing plans and free credits are unchanged."""
from alembic import op
import sqlalchemy as sa
revision='20261104_0020'
down_revision='20261006_0019'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('call_packs',sa.Column('id',sa.String(36),primary_key=True),sa.Column('user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('checkout_session_id',sa.String(255),nullable=False,unique=True),sa.Column('calls',sa.BigInteger(),nullable=False),sa.Column('used',sa.BigInteger(),nullable=False),sa.Column('activated_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_call_packs_user_id','call_packs',['user_id'])
    op.create_index('ix_call_packs_expires_at','call_packs',['expires_at'])
    op.execute("INSERT INTO subscription_plans (code,name,monthly_lookups,requests_per_minute,price_cents,currency,active,created_at) VALUES ('ONE_TIME','One-time pack',500,30,499,'USD',true,CURRENT_TIMESTAMP)")
def downgrade():
    op.drop_table('call_packs')
    op.execute("DELETE FROM subscription_plans WHERE code='ONE_TIME'")
