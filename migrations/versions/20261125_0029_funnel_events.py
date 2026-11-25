"""First-party allowlisted funnel counts; no event payloads or product data."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0029"
down_revision="20261125_0028"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("funnel_events",sa.Column("id",sa.String(36),primary_key=True),sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("visit_hash",sa.String(64)),sa.Column("event",sa.String(32),nullable=False),sa.Column("tool",sa.String(32),nullable=False),sa.Column("dedupe_key",sa.String(160)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("dedupe_key",name="uq_funnel_dedupe"))
    op.create_index("ix_funnel_created_event","funnel_events",["created_at","event"])
    op.create_index("ix_funnel_user_created","funnel_events",["user_id","created_at"])


def downgrade():op.drop_table("funnel_events")
