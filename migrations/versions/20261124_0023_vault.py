"""Private account product vault."""
from alembic import op
import sqlalchemy as sa

revision = "20261124_0023"
down_revision = "20261123_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("vault_products",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("identity_key",sa.String(64),nullable=False),
        sa.Column("barcode",sa.String(500),nullable=False),
        sa.Column("barcode_type",sa.String(20),nullable=False),
        sa.Column("name",sa.String(200),nullable=False),
        sa.Column("sku",sa.String(100),nullable=False),
        sa.Column("brand",sa.String(200),nullable=False),
        sa.Column("category",sa.String(100),nullable=False),
        sa.Column("collection",sa.String(100),nullable=False),
        sa.Column("notes",sa.Text(),nullable=False),
        sa.Column("image_url",sa.String(2048),nullable=False),
        sa.Column("price_minor",sa.BigInteger()),
        sa.Column("currency",sa.String(3),nullable=False),
        sa.Column("custom_fields",sa.JSON(),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("user_id","identity_key",name="uq_vault_user_identity"))
    op.create_index("ix_vault_user_updated","vault_products",["user_id","updated_at"])


def downgrade():
    op.drop_table("vault_products")
