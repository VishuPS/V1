"""Private asset register and append-only assignment/maintenance history."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0028"
down_revision="20261125_0027"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("asset_records",sa.Column("id",sa.String(36),primary_key=True),sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),sa.Column("product_id",sa.String(36),sa.ForeignKey("vault_products.id",ondelete="SET NULL")),
        sa.Column("asset_code",sa.String(80),nullable=False),sa.Column("name",sa.String(200),nullable=False),sa.Column("description",sa.String(4000),nullable=False),sa.Column("location",sa.String(200),nullable=False),sa.Column("assigned_person",sa.String(200),nullable=False),sa.Column("acquired_on",sa.Date()),sa.Column("purchase_minor",sa.BigInteger()),sa.Column("currency",sa.String(3),nullable=False),sa.Column("condition",sa.String(20),nullable=False),sa.Column("status",sa.String(20),nullable=False),sa.Column("checked_out_to",sa.String(200),nullable=False),sa.Column("revision",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("user_id","asset_code",name="uq_asset_owner_code"))
    op.create_index("ix_asset_owner_updated","asset_records",["user_id","updated_at"])
    op.create_index("ix_asset_product","asset_records",["product_id"])
    op.create_table("asset_events",sa.Column("id",sa.String(36),primary_key=True),sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),sa.Column("asset_id",sa.String(36),sa.ForeignKey("asset_records.id",ondelete="RESTRICT"),nullable=False),sa.Column("kind",sa.String(20),nullable=False),sa.Column("note",sa.String(1000),nullable=False),sa.Column("event_date",sa.Date()),sa.Column("before",sa.JSON(),nullable=False),sa.Column("after",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_asset_event_owner_created","asset_events",["user_id","created_at"])
    op.create_index("ix_asset_event_record","asset_events",["asset_id","created_at"])


def downgrade():
    op.drop_table("asset_events");op.drop_table("asset_records")
