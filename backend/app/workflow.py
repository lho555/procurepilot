"""Application checkpoints at business boundaries, sharing transactions with local side effects.

The read analysis uses LangGraph. This runner persists its JSON output and next business
phase in MySQL/SQLite; it does not implement the LangGraph BaseCheckpointSaver API.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import Engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent import AgentRuntime
from app.agent_routes import get_agent_runtime
from app.approval import Confirmation, change, get_request
from app.database import get_engine
from app.models import WorkflowRun, WorkflowCheckpoint
from app.routes import require, save_draft
from app.schemas import DraftInput

router = APIRouter(prefix="/workflows", tags=["durable workflow"])
LEASE_SECONDS = 60
TERMINAL = {"COMPLETED", "REJECTED"}


class StartWorkflow(Confirmation):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idempotency_key: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
    query: Annotated[str, Field(min_length=2, max_length=1000)]
    draft: DraftInput

    @field_validator("idempotency_key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        # Identical key semantics on SQLite and case-insensitive MySQL collations.
        return value.lower()


class ResumeWorkflow(Confirmation):
    execute_confirmed: StrictBool = False


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def checkpoint(session, record, phase, status, state):
    record.phase, record.status, record.state = phase, status, state
    record.revision += 1
    session.add(WorkflowCheckpoint(run_id=record.id, revision=record.revision, phase=phase, status=status, state=state, created_at=now()))


def describe(session, run_id):
    record = require(session, WorkflowRun, run_id)
    # No internal lease token, hash, or client-writable checkpoint fields in the API.
    return {"id": record.id, "idempotency_key": record.idempotency_key, "phase": record.phase, "status": record.status,
            "revision": record.revision, "state": record.state, "request_id": record.request_id,
            "busy": record.lock_until is not None and record.lock_until > now(),
            "request": get_request(record.request_id, session).model_dump(mode="json") if record.request_id else None,
            "checkpoints": [{"revision": item.revision, "phase": item.phase, "status": item.status, "created_at": item.created_at.replace(tzinfo=timezone.utc).isoformat()} for item in session.scalars(select(WorkflowCheckpoint).where(WorkflowCheckpoint.run_id == run_id).order_by(WorkflowCheckpoint.revision))]}


class DurableWorkflow:
    def __init__(self, engine: Engine, analyzer: AgentRuntime):
        self.engine, self.analyzer = engine, analyzer

    def create(self, body: StartWorkflow):
        if not body.confirmed or not body.draft.draft_creation_confirmed:
            raise HTTPException(403, "请明确确认创建草稿并提交审批；本次不授权执行")
        payload = body.model_dump(mode="json", exclude={"idempotency_key"})
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with Session(self.engine) as session:
            existing = session.scalar(select(WorkflowRun).where(WorkflowRun.idempotency_key == body.idempotency_key))
            if existing:
                if existing.input_hash != digest:
                    raise HTTPException(409, "幂等键已用于不同输入")
                return existing.id, False
            record = WorkflowRun(id=f"WF-{uuid4().hex}", idempotency_key=body.idempotency_key, input_hash=digest, input=payload, state={}, phase="ANALYZE", status="READY", revision=0, created_at=now())
            session.add(record)
            run_id = record.id
            try:
                session.flush()
                checkpoint(session, record, "ANALYZE", "READY", {})
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(WorkflowRun).where(WorkflowRun.idempotency_key == body.idempotency_key))
                if existing is None:
                    raise
                if existing.input_hash != digest:
                    raise HTTPException(409, "幂等键已用于不同输入")
                return existing.id, False
            return run_id, True

    def claim(self, run_id):
        token = uuid4().hex
        with Session(self.engine) as session:
            record = require(session, WorkflowRun, run_id)
            if record.status in TERMINAL:
                return None
            result = session.execute(update(WorkflowRun).where(WorkflowRun.id == run_id, or_(WorkflowRun.lock_until.is_(None), WorkflowRun.lock_until <= now())).values(owner_token=token, lock_until=now() + timedelta(seconds=LEASE_SECONDS)).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise HTTPException(409, "运行正在处理，请稍后刷新；中途崩溃后租约最多保留 60 秒")
            session.commit()
        return token

    def owned(self, session, run_id, token):
        result = session.execute(update(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.owner_token == token, WorkflowRun.lock_until > now()).values(lock_until=now() + timedelta(seconds=LEASE_SECONDS)).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, "运行租约已失效，旧结果未提交，请刷新后恢复")
        return require(session, WorkflowRun, run_id)

    def release(self, run_id, token):
        with Session(self.engine) as session:
            session.execute(update(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.owner_token == token).values(owner_token=None, lock_until=None))
            session.commit()

    async def drive(self, run_id, confirmation: ResumeWorkflow):
        if not confirmation.confirmed:
            raise HTTPException(403, "恢复运行必须显式确认")
        token = self.claim(run_id)
        if token is None:
            with Session(self.engine) as session:
                return describe(session, run_id)
        try:
            # At most four persisted phases per invocation; no background write retry.
            for _ in range(4):
                with Session(self.engine) as session:
                    record = require(session, WorkflowRun, run_id)
                    phase, payload = record.phase, record.input
                analysis = await self.analyzer.run(payload["query"]) if phase == "ANALYZE" else None
                with Session(self.engine) as session, session.begin():
                    record = self.owned(session, run_id, token)
                    state = dict(record.state)
                    if phase == "ANALYZE":
                        state["analysis"] = analysis
                        if analysis["status"] != "COMPLETED":
                            checkpoint(session, record, "ANALYZE", "FAILED", state)
                            break
                        if analysis["facts"]["order"]["id"] != payload["draft"]["source_order_id"]:
                            state["error"] = "分析订单与确认的草稿来源不一致，请使用新幂等键重新创建"
                            checkpoint(session, record, "STOPPED", "FAILED", state)
                            break
                        checkpoint(session, record, "DRAFT", "READY", state)
                    elif phase == "DRAFT":
                        draft = save_draft(DraftInput.model_validate(payload["draft"]), session, commit=False)
                        record.request_id = draft.id
                        change(draft.id, Confirmation(actor=payload["actor"], comment=payload["comment"], confirmed=True), session, "DRAFT", "WAITING_APPROVAL", "SUBMITTED", commit=False)
                        state["request_id"] = draft.id
                        checkpoint(session, record, "APPROVAL", "WAITING_APPROVAL", state)
                        break
                    elif phase == "APPROVAL":
                        detail = get_request(record.request_id, session)
                        if detail.request.status == "REJECTED":
                            checkpoint(session, record, "DONE", "REJECTED", state)
                            break
                        if detail.request.status != "APPROVED":
                            break
                        checkpoint(session, record, "EXECUTE", "WAITING_EXECUTION", state)
                    elif phase == "EXECUTE":
                        detail = get_request(record.request_id, session)
                        if detail.request.status != "APPROVED":
                            raise HTTPException(409, "申请未批准，不能执行")
                        if detail.action is None:
                            if not confirmation.execute_confirmed:
                                break
                            detail = change(record.request_id, confirmation, session, "APPROVED", "APPROVED", "MOCK_EXECUTED", execute=True, commit=False)
                        state["action"] = detail.action.model_dump(mode="json")
                        checkpoint(session, record, "DONE", "COMPLETED", state)
                        break
                    else:
                        break
        finally:
            # A dead process leaves a bounded lease. A live cancelled request releases its own token.
            self.release(run_id, token)
        with Session(self.engine) as session:
            return describe(session, run_id)


def get_workflow():
    return DurableWorkflow(get_engine(), get_agent_runtime())


@router.get("")
def list_workflows(workflow: Annotated[DurableWorkflow, Depends(get_workflow)]):
    with Session(workflow.engine) as session:
        return [{"id": item.id, "status": item.status, "phase": item.phase, "request_id": item.request_id} for item in session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc(), WorkflowRun.id).limit(100))]


@router.get("/{run_id}")
def get_workflow_run(run_id: str, workflow: Annotated[DurableWorkflow, Depends(get_workflow)]):
    with Session(workflow.engine) as session:
        return describe(session, run_id)


@router.post("")
async def start_workflow(body: StartWorkflow, workflow: Annotated[DurableWorkflow, Depends(get_workflow)]):
    run_id, created = workflow.create(body)
    if created:
        return await workflow.drive(run_id, ResumeWorkflow(actor=body.actor, comment=body.comment, confirmed=True))
    with Session(workflow.engine) as session:
        return describe(session, run_id)


@router.post("/{run_id}/resume")
async def resume_workflow(run_id: str, body: ResumeWorkflow, workflow: Annotated[DurableWorkflow, Depends(get_workflow)]):
    return await workflow.drive(run_id, body)
