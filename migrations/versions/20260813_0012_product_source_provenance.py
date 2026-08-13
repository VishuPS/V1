"""add normalized product source provenance

Revision ID: 20260813_0012
Revises: 20260812_0011
"""

import sqlalchemy as sa
from alembic import op


revision = "20260813_0012"
down_revision = "20260812_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_barcode", sa.String(14), sa.ForeignKey("products.barcode", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_product_id", sa.String(256), nullable=False),
        sa.Column("source_gtin", sa.String(14), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("license", sa.String(128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("source", "source_product_id", name="uq_product_sources_identity"),
    )
    op.create_index("ix_product_sources_product_source", "product_sources", ["product_barcode", "source"])
    op.create_index("ix_product_sources_source_gtin", "product_sources", ["source", "source_gtin"])
    op.create_table(
        "product_source_syncs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("dataset_url", sa.Text()),
        sa.Column("dataset_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("checkpoint_record", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("processed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inserted", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enriched", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "dataset_fingerprint", name="uq_product_source_sync_fingerprint"),
    )
    op.create_index("ix_product_source_syncs_source", "product_source_syncs", ["source"])
    # Preserve the original single-source attribution for all existing products.
    op.execute(sa.text("""
        INSERT INTO product_sources
            (id, product_barcode, source, source_product_id, source_gtin, source_url,
             license, priority, imported_at, source_updated_at, last_seen_at, source_metadata)
        SELECT
            lower(hex(randomblob(16))), barcode, CASE WHEN source = 'Open Food Facts' THEN 'OPEN_FOOD_FACTS' ELSE source END, source_id, barcode, NULL,
            CASE WHEN source = 'Open Food Facts' THEN 'ODbL-1.0' ELSE 'UNKNOWN' END,
            CASE WHEN source = 'Open Food Facts' THEN 200 ELSE 100 END,
            created_at, source_updated_at, updated_at, '{}'
        FROM products
    """) if op.get_bind().dialect.name == "sqlite" else sa.text("""
        INSERT INTO product_sources
            (id, product_barcode, source, source_product_id, source_gtin, source_url,
             license, priority, imported_at, source_updated_at, last_seen_at, source_metadata)
        SELECT
            md5(random()::text || clock_timestamp()::text || barcode), barcode, CASE WHEN source = 'Open Food Facts' THEN 'OPEN_FOOD_FACTS' ELSE source END, source_id, barcode, NULL,
            CASE WHEN source = 'Open Food Facts' THEN 'ODbL-1.0' ELSE 'UNKNOWN' END,
            CASE WHEN source = 'Open Food Facts' THEN 200 ELSE 100 END,
            created_at, source_updated_at, updated_at, '{}'::json
        FROM products
    """))


def downgrade() -> None:
    op.drop_table("product_source_syncs")
    op.drop_table("product_sources")
