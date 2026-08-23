"""add contributor ecosystem

Revision ID: 20260823_0015
Revises: 20260823_0014
"""

from alembic import op
import sqlalchemy as sa

revision = "20260823_0015"
down_revision = "20260823_0014"
branch_labels = None
depends_on = None


STATUS_CHECK = "status IN ('PENDING','APPROVED','REJECTED','NEEDS_CHANGES')"


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "product_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("submitted_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("brand_profile_id", sa.String(36)),
        sa.Column("store_profile_id", sa.String(36)),
        sa.Column("submitted_gtin", sa.String(32), nullable=False), sa.Column("canonical_gtin", sa.String(14), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False), sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("manufacturer", sa.Text()), sa.Column("category", sa.Text()), sa.Column("net_content", sa.Text()),
        sa.Column("quantity", sa.Text()), sa.Column("model", sa.Text()), sa.Column("mpn", sa.Text()), sa.Column("description", sa.Text()),
        sa.Column("country_of_sale", sa.String(120)), sa.Column("product_url", sa.Text()), sa.Column("image_url", sa.Text()),
        sa.Column("contribution_source", sa.String(32), nullable=False, server_default="USER_CONTRIBUTED"),
        sa.Column("terms_version", sa.String(32), nullable=False, server_default="2026-08"),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"), sa.Column("review_notes", sa.Text()),
        sa.Column("contributor_message", sa.Text()), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")), *_timestamps(),
        sa.UniqueConstraint("submitted_by_user_id", "canonical_gtin", name="uq_product_submission_user_gtin"),
        sa.CheckConstraint(STATUS_CHECK, name="ck_product_submission_status"),
    )
    op.create_index("ix_product_submissions_submitted_by_user_id", "product_submissions", ["submitted_by_user_id"])
    op.create_index("ix_product_submissions_canonical_gtin", "product_submissions", ["canonical_gtin"])
    op.create_index("ix_product_submissions_status", "product_submissions", ["status"])
    op.create_index("ix_product_submissions_status_created", "product_submissions", ["status", "created_at"])

    for table, prefix, business_fields in (
        ("store_submissions", "store", [sa.Column("country", sa.String(120), nullable=False), sa.Column("description", sa.Text()), sa.Column("logo_url", sa.Text()), sa.Column("contact_name", sa.String(160)), sa.Column("contact_email", sa.String(320))]),
        ("brand_submissions", "brand", [sa.Column("company", sa.String(240)), sa.Column("country", sa.String(120)), sa.Column("contact_name", sa.String(160)), sa.Column("business_email", sa.String(320)), sa.Column("description", sa.Text()), sa.Column("logo_url", sa.Text())]),
    ):
        op.create_table(
            table, sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("submitted_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False), sa.Column("normalized_name", sa.String(200), nullable=False),
            sa.Column("website", sa.Text(), nullable=False), sa.Column("normalized_website", sa.Text(), nullable=False), *business_fields,
            sa.Column("terms_version", sa.String(32), nullable=False, server_default="2026-08"),
            sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"), sa.Column("review_notes", sa.Text()),
            sa.Column("contributor_message", sa.Text()), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("reviewed_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")), *_timestamps(),
            sa.UniqueConstraint("normalized_name", "normalized_website", name=f"uq_{prefix}_submission_identity"),
            sa.CheckConstraint(STATUS_CHECK, name=f"ck_{prefix}_submission_status"),
        )
        op.create_index(f"ix_{table}_submitted_by_user_id", table, ["submitted_by_user_id"])
        op.create_index(f"ix_{table}_normalized_name", table, ["normalized_name"])
        op.create_index(f"ix_{table}_status", table, ["status"])

    op.create_table(
        "stores", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_submission_id", sa.String(36), sa.ForeignKey("store_submissions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("slug", sa.String(180), nullable=False, unique=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("website", sa.Text(), nullable=False), sa.Column("country", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("logo_url", sa.Text()), sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), *_timestamps(),
    )
    op.create_index("ix_stores_owner_user_id", "stores", ["owner_user_id"]); op.create_index("ix_stores_slug", "stores", ["slug"], unique=True)
    op.create_table(
        "brands", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_submission_id", sa.String(36), sa.ForeignKey("brand_submissions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("slug", sa.String(180), nullable=False, unique=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(240)), sa.Column("website", sa.Text(), nullable=False), sa.Column("country", sa.String(120)),
        sa.Column("description", sa.Text()), sa.Column("logo_url", sa.Text()), sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), *_timestamps(),
    )
    op.create_index("ix_brands_owner_user_id", "brands", ["owner_user_id"]); op.create_index("ix_brands_slug", "brands", ["slug"], unique=True)
    with op.batch_alter_table("product_submissions") as batch:
        batch.create_foreign_key("fk_product_submissions_brand_profile", "brands", ["brand_profile_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_product_submissions_store_profile", "stores", ["store_profile_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_product_submissions_brand_profile_id", "product_submissions", ["brand_profile_id"])
    op.create_index("ix_product_submissions_store_profile_id", "product_submissions", ["store_profile_id"])
    op.create_table(
        "product_offers", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_barcode", sa.String(14), sa.ForeignKey("products.barcode", ondelete="CASCADE"), nullable=False),
        sa.Column("product_url", sa.Text(), nullable=False), sa.Column("price_minor", sa.BigInteger()), sa.Column("currency", sa.String(3)),
        sa.Column("availability", sa.String(32)), sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"), *_timestamps(),
        sa.UniqueConstraint("store_id", "product_barcode", "product_url", name="uq_product_offer_identity"),
        sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="ck_product_offer_status"),
    )
    op.create_index("ix_product_offers_store_id", "product_offers", ["store_id"]); op.create_index("ix_product_offers_product_barcode", "product_offers", ["product_barcode"]); op.create_index("ix_product_offers_status", "product_offers", ["status"])
    op.create_table(
        "bulk_submissions", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("submitted_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False), sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("valid_row_count", sa.Integer(), nullable=False), sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False), sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("terms_version", sa.String(32), nullable=False, server_default="2026-08"),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("review_notes", sa.Text()), sa.Column("contributor_message", sa.Text()), sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")), *_timestamps(),
        sa.CheckConstraint(STATUS_CHECK, name="ck_bulk_submission_status"),
    )
    op.create_index("ix_bulk_submissions_submitted_by_user_id", "bulk_submissions", ["submitted_by_user_id"]); op.create_index("ix_bulk_submissions_status", "bulk_submissions", ["status"])


def downgrade() -> None:
    for table in ("product_offers", "product_submissions", "brands", "stores", "bulk_submissions", "brand_submissions", "store_submissions"):
        op.drop_table(table)
