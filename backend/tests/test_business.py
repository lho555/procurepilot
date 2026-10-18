from alembic import command
from alembic.config import Config
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import pytest

from app.database import get_session
from app.main import app
from app.models import InventoryItem, PurchaseRequest
from app.seed import seed

BACKEND = Path(__file__).resolve().parents[1]

DRAFT = {
    "source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01",
    "supplier_id": "SUP-B", "quantity": 78, "reason": "补充订单缺口",
    "draft_creation_confirmed": True,
}


def request_count(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(PurchaseRequest))


def test_query_chain_and_decimal_prices(client):
    order = client.get("/orders/PO-2026-10086").json()
    assert order["items"] == [{"sku": "SKU-MOTOR-01", "quantity": 100}]
    stock = client.get(f"/inventory/{order['items'][0]['sku']}").json()
    assert stock["available"] == 22
    assert order["items"][0]["quantity"] - stock["available"] == 78
    assert order["supplier_delivery_date"] > order["due_date"]
    supplier = client.get(f"/suppliers/{order['supplier_id']}").json()
    quote = next(quote for quote in supplier["quotes"] if quote["sku"] == stock["sku"])
    assert quote["unit_price"] == "120.00"
    assert quote["minimum_order_quantity"] == 50
    normal = client.get("/orders/PO-2026-10087").json()
    assert normal["supplier_delivery_date"] <= normal["due_date"]
    assert client.get("/inventory/SKU-BOLT-01").json()["available"] >= normal["items"][0]["quantity"]


@pytest.mark.parametrize("path", ["/orders/missing", "/inventory/missing", "/suppliers/missing"])
def test_missing_record(client, path):
    assert client.get(path).status_code == 404


def test_draft_is_persisted_without_changing_business_state(client, database):
    before = client.get("/inventory/SKU-MOTOR-01").json()
    order_before = client.get("/orders/PO-2026-10086").json()
    response = client.post("/purchase-requests/draft", json=DRAFT)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "DRAFT"
    assert data["created_at"].endswith("+00:00")
    with Session(database) as session:
        persisted = session.get(PurchaseRequest, data["id"])
        assert persisted.quantity == 78
        assert persisted.status == "DRAFT"
    assert client.get("/inventory/SKU-MOTOR-01").json() == before
    assert client.get("/orders/PO-2026-10086").json() == order_before


@pytest.mark.parametrize("changes,expected", [
    ({"draft_creation_confirmed": False}, 403),
    ({"draft_creation_confirmed": "true"}, 422),
    ({"draft_creation_confirmed": 1}, 422),
    ({"quantity": 0}, 422), ({"quantity": -1}, 422),
    ({"quantity": 1.5}, 422), ({"quantity": True}, 422),
    ({"quantity": 1_000_001}, 422), ({"reason": "  "}, 422),
    ({"status": "APPROVED"}, 422),
    ({"source_order_id": "missing"}, 404),
    ({"sku": "missing"}, 404), ({"supplier_id": "missing"}, 404),
    ({"sku": "SKU-BOLT-01"}, 409),
    ({"source_order_id": "PO-2026-10087", "sku": "SKU-BOLT-01"}, 409),
    ({"quantity": 19}, 409),
])
def test_rejected_draft_leaves_no_row(client, database, changes, expected):
    before = request_count(database)
    response = client.post("/purchase-requests/draft", json={**DRAFT, **changes})
    assert response.status_code == expected
    assert request_count(database) == before


def test_confirmation_is_required(client, database):
    body = {key: value for key, value in DRAFT.items() if key != "draft_creation_confirmed"}
    assert client.post("/purchase-requests/draft", json=body).status_code == 422
    assert request_count(database) == 0


def test_commit_failure_rolls_back(client, database, monkeypatch):
    def fail_commit(_session):
        raise IntegrityError("synthetic failure", {}, Exception("synthetic failure"))
    monkeypatch.setattr(Session, "commit", fail_commit)
    assert client.post("/purchase-requests/draft", json=DRAFT).status_code == 503
    assert request_count(database) == 0


def test_database_unavailable_does_not_break_health(client):
    from sqlalchemy.exc import OperationalError

    def unavailable():
        raise OperationalError("private database address", {}, Exception("secret password"))
    app.dependency_overrides[get_session] = unavailable
    response = client.get("/orders/PO-2026-10086")
    assert response.status_code == 503
    assert "secret" not in response.text
    assert "private" not in response.text
    assert client.get("/health").status_code == 200


def test_seed_repeat_preserves_modified_data(database):
    with Session(database) as session, session.begin():
        session.get(InventoryItem, "SKU-MOTOR-01").on_hand = 99
    with Session(database) as session, session.begin():
        result = seed(session)
        assert result == {"inserted": 0, "skipped": 11}
        assert session.get(InventoryItem, "SKU-MOTOR-01").on_hand == 99


def test_migration_matches_orm(database):
    command.check(Config(str(BACKEND / "alembic.ini")))


def test_inventory_constraint_is_enforced(database):
    with pytest.raises(IntegrityError):
        with Session(database) as session, session.begin():
            session.get(InventoryItem, "SKU-MOTOR-01").reserved = 31
            session.flush()
