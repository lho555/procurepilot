from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (CheckConstraint("lead_time_days >= 0", name="ck_supplier_lead_time"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    lead_time_days: Mapped[int]
    quotes: Mapped[list["SupplierQuote"]] = relationship(lazy="selectin", order_by="SupplierQuote.id")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_inventory_on_hand"),
        CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_inventory_reserved"),
    )
    sku: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    on_hand: Mapped[int]
    reserved: Mapped[int]

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"
    __table_args__ = (
        UniqueConstraint("supplier_id", "sku", name="uq_quote_supplier_sku"),
        CheckConstraint("unit_price > 0", name="ck_quote_price"),
        CheckConstraint("minimum_order_quantity > 0", name="ck_quote_moq"),
        CheckConstraint("lead_time_days >= 0", name="ck_quote_lead_time"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    sku: Mapped[str] = mapped_column(ForeignKey("inventory_items.sku"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    minimum_order_quantity: Mapped[int]
    lead_time_days: Mapped[int]


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    due_date: Mapped[date] = mapped_column(Date)
    supplier_delivery_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[list["PurchaseOrderItem"]] = relationship(lazy="selectin", order_by="PurchaseOrderItem.sku")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_order_item_quantity"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("inventory_items.sku"), primary_key=True)
    quantity: Mapped[int]


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    __table_args__ = (
        ForeignKeyConstraint(["source_order_id", "sku"], ["purchase_order_items.order_id", "purchase_order_items.sku"]),
        CheckConstraint("quantity > 0", name="ck_request_quantity"),
        CheckConstraint("status IN ('DRAFT', 'WAITING_APPROVAL', 'APPROVED', 'REJECTED')", name="ck_request_status"),
        CheckConstraint("mock_executed_at IS NULL OR status = 'APPROVED'", name="ck_request_execution_approved"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_order_id: Mapped[str] = mapped_column(String(40))
    sku: Mapped[str] = mapped_column(String(40))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    quantity: Mapped[int]
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    mock_executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApprovalEvent(Base):
    __tablename__ = "approval_events"
    __table_args__ = (CheckConstraint("event IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'MOCK_EXECUTED')", name="ck_approval_event"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    event: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(80))
    comment: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MockAction(Base):
    __tablename__ = "mock_actions"
    # Exactly one local mock receipt per request. No external purchase side effect.
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"), primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON)
    phase: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(default=0)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_requests.id"), nullable=True, unique=True)
    owner_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lock_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    phase: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24))
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
