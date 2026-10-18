"""Stage 1 core business tables (no seed or automatic business actions)."""
from alembic import op
import sqlalchemy as sa

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_supplier_lead_time"),
    )
    op.create_table(
        "inventory_items",
        sa.Column("sku", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.CheckConstraint("on_hand >= 0", name="ck_inventory_on_hand"),
        sa.CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_inventory_reserved"),
    )
    op.create_table(
        "supplier_quotes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("supplier_id", sa.String(40), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("sku", sa.String(40), sa.ForeignKey("inventory_items.sku"), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.UniqueConstraint("supplier_id", "sku", name="uq_quote_supplier_sku"),
        sa.CheckConstraint("unit_price > 0", name="ck_quote_price"),
        sa.CheckConstraint("minimum_order_quantity > 0", name="ck_quote_moq"),
        sa.CheckConstraint("lead_time_days >= 0", name="ck_quote_lead_time"),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("supplier_id", sa.String(40), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("supplier_delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_table(
        "purchase_order_items",
        sa.Column("order_id", sa.String(40), sa.ForeignKey("purchase_orders.id"), primary_key=True),
        sa.Column("sku", sa.String(40), sa.ForeignKey("inventory_items.sku"), primary_key=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_item_quantity"),
    )
    op.create_table(
        "purchase_requests",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("source_order_id", sa.String(40), nullable=False),
        sa.Column("sku", sa.String(40), nullable=False),
        sa.Column("supplier_id", sa.String(40), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_order_id", "sku"], ["purchase_order_items.order_id", "purchase_order_items.sku"]),
        sa.CheckConstraint("quantity > 0", name="ck_request_quantity"),
        sa.CheckConstraint("status = 'DRAFT'", name="ck_request_draft_only"),
    )


def downgrade() -> None:
    for table in ("purchase_requests", "purchase_order_items", "purchase_orders", "supplier_quotes", "inventory_items", "suppliers"):
        op.drop_table(table)
