"""Verify Stage 1 using local MySQL; explicit confirmation permits demo writes."""
import argparse
import json
import os
import sys

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import URL, select, text
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_engine
from app.main import app
from app.models import PurchaseRequest
from app.seed import seed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Approve local MySQL migration, synthetic seed and one DRAFT save")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required for these local database writes")
    # Use only the Compose database on loopback, never an external DATABASE_URL.
    url = URL.create(
        "mysql+pymysql", username=os.getenv("MYSQL_USER", "procurepilot"),
        password=os.getenv("MYSQL_PASSWORD", "procurepilot_dev"), host="127.0.0.1",
        port=int(os.getenv("MYSQL_PORT", "3307")), database=os.getenv("MYSQL_DATABASE", "procurepilot"),
        query={"charset": "utf8mb4"},
    )
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_engine.cache_clear()
    checks = []
    engine = None
    report_path = PROJECT_ROOT / ".local/mysql-verification.json"
    try:
        engine = get_engine()
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
        checks.append({"check": "mysql_connection", "status": "pass", "version": version})
        config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        checks.append({"check": "migration_and_model_consistency", "status": "pass"})
        with Session(engine) as session, session.begin():
            seeded = seed(session)
        with Session(engine) as session, session.begin():
            repeated = seed(session)
        if repeated["inserted"]:
            raise ValueError("Seed unexpectedly inserted rows on repeat")
        checks.append({"check": "seed_repeat", "status": "pass", "first": seeded, "repeat": repeated})
        with TestClient(app) as client:
            order_response = client.get("/orders/PO-2026-10086")
            stock_response = client.get("/inventory/SKU-MOTOR-01")
            supplier_response = client.get("/suppliers/SUP-A")
            if any(response.status_code != 200 for response in (order_response, stock_response, supplier_response)):
                raise ValueError("Business query returned a non-200 response")
            order, stock = order_response.json(), stock_response.json()
            if order["items"][0]["quantity"] - stock["available"] != 78:
                raise ValueError("Demo data differs from the expected shortage 78")
            checks.append({"check": "order_inventory_supplier", "status": "pass", "shortage": 78})
            body = {"source_order_id": order["id"], "sku": stock["sku"], "supplier_id": "SUP-B", "quantity": 78, "reason": "本机 MySQL 验证：合成草稿", "draft_creation_confirmed": False}
            if client.post("/purchase-requests/draft", json=body).status_code != 403:
                raise ValueError("Unconfirmed draft was not rejected")
            body["draft_creation_confirmed"] = True
            response = client.post("/purchase-requests/draft", json=body)
            if response.status_code != 201 or response.json()["status"] != "DRAFT":
                raise ValueError("Confirmed draft was not saved")
            with Session(engine) as session:
                draft = session.scalar(select(PurchaseRequest).where(PurchaseRequest.id == response.json()["id"]))
                if draft is None or draft.status != "DRAFT":
                    raise ValueError("Draft not persisted in MySQL")
            if client.get("/inventory/SKU-MOTOR-01").json() != stock or client.get("/orders/PO-2026-10086").json() != order:
                raise ValueError("Draft changed inventory or order")
            checks.append({"check": "confirmation_and_draft_persistence", "status": "pass", "id": response.json()["id"]})
        report = {"passed": True, "database": "mysql", "checks": checks}
    except Exception as error:
        # Do not output raw database exceptions, SQL parameters or credentials.
        report = {"passed": False, "database": "mysql", "checks": checks, "error_type": type(error).__name__, "action": "Check Docker Compose, local MYSQL_* settings and installed backend dependencies."}
    finally:
        if engine is not None:
            engine.dispose()
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_engine.cache_clear()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
