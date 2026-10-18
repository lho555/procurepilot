from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import InventoryItem, PurchaseOrder, PurchaseOrderItem, PurchaseRequest, Supplier, SupplierQuote
from app.schemas import DraftInput, DraftResponse, InventoryResponse, OrderResponse, SupplierResponse

router = APIRouter()
Database = Annotated[Session, Depends(get_session)]


def require(session: Session, model, key):
    record = session.get(model, key)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, session: Database):
    return require(session, PurchaseOrder, order_id)


@router.get("/inventory/{sku}", response_model=InventoryResponse)
def get_inventory(sku: str, session: Database):
    return require(session, InventoryItem, sku)


@router.get("/suppliers/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: str, session: Database):
    return require(session, Supplier, supplier_id)


def save_draft(body: DraftInput, session: Session, *, commit: bool = True):
    if not body.draft_creation_confirmed:
        raise HTTPException(status_code=403, detail="保存草稿前必须显式确认")
    require(session, PurchaseOrder, body.source_order_id)
    require(session, InventoryItem, body.sku)
    require(session, Supplier, body.supplier_id)
    if session.get(PurchaseOrderItem, (body.source_order_id, body.sku)) is None:
        raise HTTPException(status_code=409, detail="物料不属于来源订单")
    quote = session.scalar(select(SupplierQuote).where(
        SupplierQuote.supplier_id == body.supplier_id, SupplierQuote.sku == body.sku,
    ))
    if quote is None:
        raise HTTPException(status_code=409, detail="供应商没有该物料的报价")
    if body.quantity < quote.minimum_order_quantity:
        raise HTTPException(status_code=409, detail="数量低于供应商最小起订量")
    draft = PurchaseRequest(
        id=f"PR-{uuid4().hex}",
        **body.model_dump(exclude={"draft_creation_confirmed"}),
        status="DRAFT",
    )
    session.add(draft)
    session.flush()
    response = DraftResponse.model_validate(draft)
    if commit:
        session.commit()
    return response


@router.post("/purchase-requests/draft", response_model=DraftResponse, status_code=201)
def create_draft(body: DraftInput, session: Database):
    return save_draft(body, session)
