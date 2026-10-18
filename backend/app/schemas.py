from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_serializer

Identifier = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(ReadModel):
    sku: str
    quantity: int


class OrderResponse(ReadModel):
    id: str
    supplier_id: str
    due_date: date
    supplier_delivery_date: date
    status: str
    items: list[OrderItemResponse]


class InventoryResponse(ReadModel):
    sku: str
    name: str
    on_hand: int
    reserved: int
    available: int


class QuoteResponse(ReadModel):
    id: str
    sku: str
    unit_price: Decimal
    currency: str
    minimum_order_quantity: int
    lead_time_days: int


class SupplierResponse(ReadModel):
    id: str
    name: str
    lead_time_days: int
    quotes: list[QuoteResponse]


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_order_id: Identifier
    sku: Identifier
    supplier_id: Identifier
    quantity: Annotated[int, Field(strict=True, gt=0, le=1_000_000)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    # Strict boolean prevents 1 / "true" from being treated as human confirmation.
    draft_creation_confirmed: StrictBool


class DraftResponse(ReadModel):
    id: str
    source_order_id: str
    sku: str
    supplier_id: str
    quantity: int
    reason: str
    status: Literal["DRAFT"]
    created_at: datetime

    @field_serializer("created_at")
    def utc_timestamp(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()
