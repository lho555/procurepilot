"""Human-confirmed local state transitions. No external action or automatic write retry."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_serializer
from sqlalchemy import case, select, update

from app.models import ApprovalEvent, MockAction, PurchaseRequest
from app.routes import Database, require
from app.schemas import DraftResponse, ReadModel

router = APIRouter(prefix="/purchase-requests", tags=["approval"])


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: Annotated[str, Field(min_length=1, max_length=80)]
    comment: Annotated[str, Field(min_length=1, max_length=500)]
    confirmed: StrictBool


class Decision(Confirmation):
    decision: Literal["APPROVED", "REJECTED"]


class RequestResponse(DraftResponse):
    status: Literal["DRAFT", "WAITING_APPROVAL", "APPROVED", "REJECTED"]


class EventResponse(ReadModel):
    id: str
    event: str
    actor: str
    comment: str
    created_at: datetime

    @field_serializer("created_at")
    def timestamp(self, value: datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()


class ActionResponse(ReadModel):
    request_id: str
    reference: str
    actor: str
    created_at: datetime

    @field_serializer("created_at")
    def timestamp(self, value: datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()


class RequestDetail(BaseModel):
    request: RequestResponse
    events: list[EventResponse]
    action: ActionResponse | None


@router.get("", response_model=list[RequestResponse])
def list_requests(session: Database):
    return session.scalars(select(PurchaseRequest).order_by(PurchaseRequest.created_at.desc(), PurchaseRequest.id).limit(100)).all()


@router.get("/{request_id}", response_model=RequestDetail)
def get_request(request_id: str, session: Database):
    record = require(session, PurchaseRequest, request_id)
    return RequestDetail(
        request=RequestResponse.model_validate(record),
        # MySQL DATETIME can share a timestamp within one second. This linear state machine
        # has a stable causal order, so random UUIDs must never decide event ordering.
        events=[EventResponse.model_validate(event) for event in session.scalars(select(ApprovalEvent).where(ApprovalEvent.request_id == request_id).order_by(case({"SUBMITTED": 0, "APPROVED": 1, "REJECTED": 1, "MOCK_EXECUTED": 2}, value=ApprovalEvent.event), ApprovalEvent.created_at, ApprovalEvent.id))],
        action=ActionResponse.model_validate(action) if (action := session.get(MockAction, request_id)) else None,
    )


def change(request_id, body, session, expected, target, event, *, execute=False, commit=True):
    if not body.confirmed:
        raise HTTPException(403, "此操作必须经人工显式确认")
    require(session, PurchaseRequest, request_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conditions = [PurchaseRequest.id == request_id, PurchaseRequest.status == expected]
    values = {"status": target}
    if execute:
        conditions.append(PurchaseRequest.mock_executed_at.is_(None))
        values["mock_executed_at"] = now
    # Compare-and-set under the database write lock also prevents concurrent decisions / execution.
    result = session.execute(update(PurchaseRequest).where(*conditions).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "当前状态不允许此操作，或 Mock Action 已执行；请刷新后核对")
    session.add(ApprovalEvent(id=f"AE-{uuid4().hex}", request_id=request_id, event=event, actor=body.actor, comment=body.comment, created_at=now))
    if execute:
        session.add(MockAction(request_id=request_id, reference=f"MOCK-{uuid4().hex}", actor=body.actor, created_at=now))
    session.flush()
    session.expire_all()
    response = get_request(request_id, session)
    if commit:
        session.commit()
    return response


@router.post("/{request_id}/submit", response_model=RequestDetail)
def submit(request_id: str, body: Confirmation, session: Database):
    return change(request_id, body, session, "DRAFT", "WAITING_APPROVAL", "SUBMITTED")


@router.post("/{request_id}/decision", response_model=RequestDetail)
def decide(request_id: str, body: Decision, session: Database):
    return change(request_id, body, session, "WAITING_APPROVAL", body.decision, body.decision)


@router.post("/{request_id}/mock-action", response_model=RequestDetail)
def mock_action(request_id: str, body: Confirmation, session: Database):
    return change(request_id, body, session, "APPROVED", "APPROVED", "MOCK_EXECUTED", execute=True)
