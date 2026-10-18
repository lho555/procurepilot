from concurrent.futures import ThreadPoolExecutor

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.approval import Confirmation, Decision, decide, mock_action
from app.config import PROJECT_ROOT
from app.main import app
from app.models import ApprovalEvent, MockAction, PurchaseRequest
from app.database import build_engine
from app.seed import seed

DRAFT = {"source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01", "supplier_id": "SUP-B", "quantity": 78, "reason": "合成审批验证", "draft_creation_confirmed": True}
CONFIRM = {"actor": "演示操作者", "comment": "人工核对合成申请", "confirmed": True}


@pytest.fixture
def request_id(client):
    response = client.post("/purchase-requests/draft", json=DRAFT)
    assert response.status_code == 201
    return response.json()["id"]


def post(client, request_id, action, **extra):
    return client.post(f"/purchase-requests/{request_id}/{action}", json={**CONFIRM, **extra})


def test_rejected_never_executes_or_changes_stock(client, request_id):
    before = client.get("/inventory/SKU-MOTOR-01").json()
    assert post(client, request_id, "submit").json()["request"]["status"] == "WAITING_APPROVAL"
    rejected = post(client, request_id, "decision", decision="REJECTED").json()
    assert rejected["request"]["status"] == "REJECTED"
    for action, extra in [("mock-action", {}), ("submit", {}), ("decision", {"decision": "APPROVED"})]:
        assert post(client, request_id, action, **extra).status_code == 409
    detail = client.get(f"/purchase-requests/{request_id}").json()
    assert detail["action"] is None
    assert [event["event"] for event in detail["events"]] == ["SUBMITTED", "REJECTED"]
    assert client.get("/inventory/SKU-MOTOR-01").json() == before


def test_approved_requires_separate_execution_confirmation(client, request_id):
    assert post(client, request_id, "decision", decision="APPROVED").status_code == 409
    assert post(client, request_id, "mock-action").status_code == 409
    post(client, request_id, "submit")
    assert post(client, request_id, "submit").status_code == 409
    assert post(client, request_id, "mock-action").status_code == 409
    approved = post(client, request_id, "decision", decision="APPROVED").json()
    assert approved["action"] is None
    assert post(client, request_id, "mock-action", confirmed=False).status_code == 403
    response = post(client, request_id, "mock-action")
    assert response.status_code == 200
    receipt = response.json()["action"]
    assert receipt["reference"].startswith("MOCK-")
    assert post(client, request_id, "mock-action").status_code == 409
    assert post(client, request_id, "decision", decision="REJECTED").status_code == 409
    assert client.get(f"/purchase-requests/{request_id}").json()["action"] == receipt
    assert len(client.get("/purchase-requests").json()) == 1


@pytest.mark.parametrize("action,extra", [("submit", {}), ("decision", {"decision": "APPROVED"}), ("mock-action", {})])
@pytest.mark.parametrize("confirmation,expected", [(False, 403), ("true", 422), (1, 422), (None, 422)])
def test_all_writes_require_strict_confirmation(client, request_id, action, extra, confirmation, expected):
    assert post(client, request_id, action, **extra, confirmed=confirmation).status_code == expected
    detail = client.get(f"/purchase-requests/{request_id}").json()
    assert detail["request"]["status"] == "DRAFT" and detail["events"] == [] and detail["action"] is None


def test_unknown_request_and_untrusted_fields(client, request_id):
    assert post(client, "PR-missing", "submit").status_code == 404
    for payload in [{**CONFIRM, "actor": " "}, {**CONFIRM, "status": "APPROVED"}, {**CONFIRM, "decision": "EXECUTED"}]:
        assert client.post(f"/purchase-requests/{request_id}/decision", json=payload).status_code == 422


def test_same_second_audit_is_in_causal_order(client, request_id, database):
    post(client, request_id, "submit")
    post(client, request_id, "decision", decision="REJECTED")
    with database.begin() as connection:
        connection.execute(text("UPDATE approval_events SET id='AE-z',created_at='2026-10-03 00:00:00' WHERE event='SUBMITTED'"))
        connection.execute(text("UPDATE approval_events SET id='AE-a',created_at='2026-10-03 00:00:00' WHERE event='REJECTED'"))
    assert [event["event"] for event in client.get(f"/purchase-requests/{request_id}").json()["events"]] == ["SUBMITTED", "REJECTED"]


def test_failure_rolls_back_state_audit_and_receipt(client, request_id, database, monkeypatch):
    post(client, request_id, "submit")
    post(client, request_id, "decision", decision="APPROVED")
    original = Session.flush
    def fail_receipt(self, *args, **kwargs):
        if any(isinstance(record, MockAction) for record in self.new):
            raise SQLAlchemyError("synthetic failure")
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_receipt)
        assert post(client, request_id, "mock-action").status_code == 503
    with Session(database) as session:
        assert session.get(PurchaseRequest, request_id).mock_executed_at is None
        assert session.get(MockAction, request_id) is None
        assert session.scalar(select(func.count()).select_from(ApprovalEvent)) == 2
    assert post(client, request_id, "mock-action").status_code == 200


def test_concurrent_decisions_and_execution_have_one_winner(client, request_id, database):
    post(client, request_id, "submit")
    def decision(value):
        with Session(database) as session:
            try:
                return decide(request_id, Decision(**CONFIRM, decision=value), session).request.status
            except Exception as error:
                assert getattr(error, "status_code", None) == 409
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decision, ["APPROVED", "REJECTED"]))
    assert outcomes.count("CONFLICT") == 1
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(ApprovalEvent)) == 2
    # If rejection won, execution remains impossible; use a fresh approved request to exercise the receipt race.
    second = client.post("/purchase-requests/draft", json=DRAFT).json()["id"]
    post(client, second, "submit")
    post(client, second, "decision", decision="APPROVED")
    def execute(_):
        with Session(database) as session:
            try:
                return mock_action(second, Confirmation(**CONFIRM), session).action.reference
            except Exception as error:
                assert getattr(error, "status_code", None) == 409
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, range(2)))
    assert outcomes.count("CONFLICT") == 1
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(MockAction)) == 1


def test_downgrade_refuses_to_discard_approval_history(client, request_id):
    post(client, request_id, "submit")
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(Config(str(PROJECT_ROOT / "backend/alembic.ini")), "0001_core")
    assert client.get(f"/purchase-requests/{request_id}").json()["request"]["status"] == "WAITING_APPROVAL"


def test_migration_preserves_stage1_draft_and_safe_empty_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
    command.upgrade(config, "0001_core")
    engine = build_engine(url)
    try:
        with Session(engine) as session, session.begin():
            seed(session)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO purchase_requests (id,source_order_id,sku,supplier_id,quantity,reason,status,created_at) VALUES ('PR-legacy','PO-2026-10086','SKU-MOTOR-01','SUP-B',78,'legacy','DRAFT','2026-10-03 00:00:00')"))
        command.upgrade(config, "head")
        command.check(config)
        with Session(engine) as session:
            record = session.get(PurchaseRequest, "PR-legacy")
            assert record.status == "DRAFT" and record.quantity == 78 and record.mock_executed_at is None
        command.downgrade(config, "0001_core")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT quantity FROM purchase_requests WHERE id='PR-legacy'")) == 78
        command.upgrade(config, "head")
    finally:
        engine.dispose()
