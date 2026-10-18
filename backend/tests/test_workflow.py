import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction

from app.agent import AgentRuntime
from app.agent_tools import ReadTools
from app.config import PROJECT_ROOT
from app.database import build_engine
from app.knowledge import KnowledgeStore, build_index, save_index
from app.main import app
from app.models import MockAction, PurchaseRequest, WorkflowCheckpoint, WorkflowRun
from app.workflow import DurableWorkflow, ResumeWorkflow, StartWorkflow, get_workflow, now

BODY = {"idempotency_key": "stage6-example", "query": "分析 PO-2026-10086", "actor": "演示操作者", "comment": "确认保存并提交审批", "confirmed": True,
        "draft": {"source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01", "supplier_id": "SUP-B", "quantity": 78, "reason": "Stage 6 合成缺口", "draft_creation_confirmed": True}}
CONFIRM = {"actor": "演示操作者", "comment": "人工核对", "confirmed": True}


class CountingAnalyzer:
    def __init__(self, runtime):
        self.runtime, self.calls = runtime, 0
    async def run(self, query):
        self.calls += 1
        return await self.runtime.run(query)


@pytest.fixture
def workflow(database, tmp_path):
    path = tmp_path / "knowledge.json"
    save_index(build_index(PROJECT_ROOT / "sample_data/policies"), path)
    analyzer = CountingAnalyzer(AgentRuntime(ReadTools(database, KnowledgeStore(path))))
    return DurableWorkflow(database, analyzer)


@pytest.fixture
def workflow_client(client, workflow):
    app.dependency_overrides[get_workflow] = lambda: workflow
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_workflow, None)


def start(client, **changes):
    response = client.post("/workflows", json={**BODY, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_restart_resume_no_query_draft_or_action_replay(workflow_client, workflow, database):
    result = start(workflow_client)
    assert result["status"] == "WAITING_APPROVAL" and result["state"]["analysis"]["status"] == "COMPLETED"
    run_id, request_id = result["id"], result["request_id"]
    class NoRead:
        async def run(self, _query):
            raise AssertionError("Recovered workflow repeated analysis")
    fresh = DurableWorkflow(build_engine(database.url), NoRead())
    try:
        pending = asyncio.run(fresh.drive(run_id, ResumeWorkflow(**CONFIRM)))
        assert pending["status"] == "WAITING_APPROVAL" and pending["revision"] == result["revision"]
        assert workflow_client.post(f"/purchase-requests/{request_id}/decision", json={**CONFIRM, "decision": "APPROVED"}).status_code == 200
        preview = asyncio.run(fresh.drive(run_id, ResumeWorkflow(**CONFIRM)))
        assert preview["status"] == "WAITING_EXECUTION" and preview["request"]["action"] is None
        completed = asyncio.run(fresh.drive(run_id, ResumeWorkflow(**CONFIRM, execute_confirmed=True)))
        assert completed["status"] == "COMPLETED"
        assert asyncio.run(fresh.drive(run_id, ResumeWorkflow(**CONFIRM, execute_confirmed=True))) == completed
    finally:
        fresh.engine.dispose()
    assert workflow.analyzer.calls == 1
    assert start(workflow_client)["id"] == run_id
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 1
        assert session.scalar(select(func.count()).select_from(MockAction)) == 1


def test_rejection_ends_graph_without_action(workflow_client):
    result = start(workflow_client)
    workflow_client.post(f"/purchase-requests/{result['request_id']}/decision", json={**CONFIRM, "decision": "REJECTED"})
    response = workflow_client.post(f"/workflows/{result['id']}/resume", json={**CONFIRM, "execute_confirmed": True}).json()
    assert response["status"] == "REJECTED" and response["request"]["action"] is None


def test_idempotency_key_conflict_and_concurrent_creation(workflow_client, workflow, database):
    def create(_):
        return workflow.create(StartWorkflow(**BODY))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, range(2)))
    assert len(set(ids)) == 1
    assert workflow_client.post("/workflows", json={**BODY, "comment": "different"}).status_code == 409
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
    assert workflow.create(StartWorkflow(**{**BODY, "idempotency_key": BODY["idempotency_key"].upper()}))[0] == ids[0]


def test_expired_worker_fenced_and_fresh_worker_recovers(workflow, database):
    run_id, _ = workflow.create(StartWorkflow(**BODY))
    old = workflow.claim(run_id)
    with pytest.raises(Exception) as blocked:
        workflow.claim(run_id)
    assert blocked.value.status_code == 409
    with Session(database) as session, session.begin():
        session.get(WorkflowRun, run_id).lock_until = now() - timedelta(seconds=1)
    new = workflow.claim(run_id)
    with Session(database) as session, session.begin():
        with pytest.raises(Exception) as fenced:
            workflow.owned(session, run_id, old)
        assert fenced.value.status_code == 409
    workflow.release(run_id, old)
    with Session(database) as session:
        assert session.get(WorkflowRun, run_id).owner_token == new
    workflow.release(run_id, new)
    assert asyncio.run(workflow.drive(run_id, ResumeWorkflow(**CONFIRM)))["status"] == "WAITING_APPROVAL"


def test_draft_checkpoint_failure_rolls_back_business_write(workflow_client, workflow, database, monkeypatch):
    original = Session.flush
    def fail(self, *args, **kwargs):
        if any(isinstance(item, WorkflowCheckpoint) and item.phase == "APPROVAL" for item in self.new):
            raise SQLAlchemyError("synthetic checkpoint failure")
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail)
        response = workflow_client.post("/workflows", json=BODY)
        assert response.status_code == 503
    with Session(database) as session:
        record = session.scalar(select(WorkflowRun))
        assert record.phase == "DRAFT"
        assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 0
        run_id = record.id
    recovered = workflow_client.post(f"/workflows/{run_id}/resume", json=CONFIRM).json()
    assert recovered["status"] == "WAITING_APPROVAL" and workflow.analyzer.calls == 1


def test_execution_checkpoint_failure_rolls_back_receipt(workflow_client, database, monkeypatch):
    result = start(workflow_client)
    workflow_client.post(f"/purchase-requests/{result['request_id']}/decision", json={**CONFIRM, "decision": "APPROVED"})
    original = Session.flush
    def fail(self, *args, **kwargs):
        if any(isinstance(item, WorkflowCheckpoint) and item.status == "COMPLETED" for item in self.new):
            raise SQLAlchemyError("synthetic checkpoint failure")
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail)
        assert workflow_client.post(f"/workflows/{result['id']}/resume", json={**CONFIRM, "execute_confirmed": True}).status_code == 503
    with Session(database) as session:
        assert session.get(MockAction, result["request_id"]) is None
    recovered = workflow_client.post(f"/workflows/{result['id']}/resume", json={**CONFIRM, "execute_confirmed": True}).json()
    assert recovered["status"] == "COMPLETED"


@pytest.mark.parametrize("field,value", [("confirmed", False), ("confirmed", "true"), ("confirmed", 1)])
def test_confirmation_and_state_injection_rejected(workflow_client, field, value):
    assert workflow_client.post("/workflows", json={**BODY, field: value}).status_code in {403, 422}
    assert workflow_client.post("/workflows", json={**BODY, "phase": "DONE"}).status_code == 422


def test_wrong_order_stops_before_draft(workflow_client, database):
    result = start(workflow_client, query="分析 PO-2026-10087")
    assert result["status"] == "FAILED" and result["phase"] == "STOPPED" and result["request_id"] is None


@pytest.mark.parametrize("at_action", [False, True])
def test_lost_response_after_commit_is_safe_to_replay(workflow_client, workflow, database, monkeypatch, at_action):
    result = start(workflow_client) if at_action else None
    if at_action:
        workflow_client.post(f"/purchase-requests/{result['request_id']}/decision", json={**CONFIRM, "decision": "APPROVED"})
    original = SessionTransaction.commit
    def lose_response(self, *args, **kwargs):
        lost = self.parent is None and any(isinstance(item, WorkflowCheckpoint) and item.status == ("COMPLETED" if at_action else "WAITING_APPROVAL") for item in self.session.new)
        original(self, *args, **kwargs)
        if lost:
            raise SQLAlchemyError("response lost after successful commit")
    with monkeypatch.context() as patch:
        patch.setattr(SessionTransaction, "commit", lose_response)
        response = workflow_client.post(f"/workflows/{result['id']}/resume", json={**CONFIRM, "execute_confirmed": True}) if at_action else workflow_client.post("/workflows", json=BODY)
        assert response.status_code == 503
    replay = start(workflow_client)
    assert replay["status"] == ("COMPLETED" if at_action else "WAITING_APPROVAL")
    assert workflow.analyzer.calls == 1
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == 1
        assert session.scalar(select(func.count()).select_from(MockAction)) == (1 if at_action else 0)


def test_cancelled_read_releases_lease_and_preserves_checkpoint(workflow, database):
    class Slow:
        async def run(self, _query):
            await asyncio.sleep(30)
    run_id, _ = workflow.create(StartWorkflow(**BODY))
    slow = DurableWorkflow(database, Slow())
    async def check():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await slow.drive(run_id, ResumeWorkflow(**CONFIRM))
    asyncio.run(check())
    with Session(database) as session:
        record = session.get(WorkflowRun, run_id)
        assert record.phase == "ANALYZE" and record.owner_token is None and record.lock_until is None
    assert asyncio.run(workflow.drive(run_id, ResumeWorkflow(**CONFIRM)))["status"] == "WAITING_APPROVAL"
