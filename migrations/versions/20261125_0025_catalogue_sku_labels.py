"""Extend private products; add SKU allocation and label templates."""
from alembic import op
import sqlalchemy as sa
revision="20261125_0025"
down_revision="20261125_0024"
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table("vault_products") as batch:
        batch.add_column(sa.Column("supplier",sa.String(200),nullable=False,server_default=""))
        batch.add_column(sa.Column("tags",sa.JSON(),nullable=False,server_default="[]"))
        batch.add_column(sa.Column("variant_of",sa.String(36)))
        batch.create_foreign_key("fk_vault_variant_parent","vault_products",["variant_of"],["id"],ondelete="SET NULL")
    op.create_index("ix_vault_variant_parent","vault_products",["variant_of"])
    op.create_table("product_identifiers",sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("product_id",sa.String(36),sa.ForeignKey("vault_products.id",ondelete="CASCADE"),nullable=False),
        sa.Column("identity_key",sa.String(64),nullable=False),sa.Column("value",sa.String(500),nullable=False),
        sa.Column("kind",sa.String(20),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("user_id","identity_key",name="uq_identifier_owner_identity"))
    op.create_index("ix_identifier_owner_product","product_identifiers",["user_id","product_id"])
    op.create_index("ix_identifier_product","product_identifiers",["product_id"])
    op.create_table("sku_sequences",sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("sequence_key",sa.String(64),nullable=False),sa.Column("prefix",sa.String(20),nullable=False),
        sa.Column("category_code",sa.String(20),nullable=False),sa.Column("pattern",sa.String(100),nullable=False),
        sa.Column("next_value",sa.BigInteger(),nullable=False),sa.UniqueConstraint("user_id","sequence_key",name="uq_sku_owner_sequence"))
    op.create_table("sku_allocations",sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("product_id",sa.String(36),sa.ForeignKey("vault_products.id",ondelete="SET NULL")),
        sa.Column("code",sa.String(80),nullable=False),sa.Column("category",sa.String(100),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("user_id","code",name="uq_sku_owner_code"))
    op.create_index("ix_sku_owner_created","sku_allocations",["user_id","created_at"])
    op.create_index("ix_sku_product","sku_allocations",["product_id"])
    op.create_table("label_templates",sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("name",sa.String(100),nullable=False),sa.Column("definition",sa.JSON(),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint("user_id","name",name="uq_label_owner_name"))
    op.create_index("ix_label_owner_updated","label_templates",["user_id","updated_at"])


def downgrade():
    for table in ["label_templates","sku_allocations","sku_sequences","product_identifiers"]:
        op.drop_table(table)
    op.drop_index("ix_vault_variant_parent",table_name="vault_products")
    with op.batch_alter_table("vault_products") as batch:
        batch.drop_constraint("fk_vault_variant_parent",type_="foreignkey")
        for column in ["variant_of","tags","supplier"]:
            batch.drop_column(column)
