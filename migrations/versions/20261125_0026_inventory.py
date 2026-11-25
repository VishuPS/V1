"""Private inventory balances and immutable movement records."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0026"
down_revision="20261125_0025"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("inventory_items",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("product_id",sa.String(36),sa.ForeignKey("vault_products.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("quantity",sa.BigInteger(),nullable=False),sa.Column("low_stock_threshold",sa.BigInteger(),nullable=False),
        sa.Column("location",sa.String(200),nullable=False),sa.Column("purchase_minor",sa.BigInteger()),
        sa.Column("currency",sa.String(3),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),sa.Column("archived",sa.Boolean(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("user_id","product_id",name="uq_inventory_product"),
        sa.CheckConstraint("quantity >= 0 AND quantity <= 1000000000",name="ck_inventory_quantity"))
    op.create_index("ix_inventory_owner","inventory_items",["user_id","updated_at"])
    op.create_table("stock_movements",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("inventory_id",sa.String(36),sa.ForeignKey("inventory_items.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("request_id",sa.String(64),nullable=False),sa.Column("delta",sa.BigInteger(),nullable=False),
        sa.Column("balance",sa.BigInteger(),nullable=False),sa.Column("reason",sa.String(500),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("user_id","request_id",name="uq_stock_request"))
    op.create_index("ix_stock_owner_created","stock_movements",["user_id","created_at"])
    op.create_index("ix_stock_item","stock_movements",["inventory_id","created_at"])


def downgrade():
    op.drop_table("stock_movements")
    op.drop_table("inventory_items")
