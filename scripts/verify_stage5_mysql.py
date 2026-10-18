"""Verify local MySQL migration and synthetic human approval with explicit --confirm."""
import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import URL, text
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.config import PROJECT_ROOT
from app.database import get_engine
from app.main import app
from stage5_demo import run_demo
from app.approval import Confirmation, Decision, decide, mock_action


def concurrency_check(client, engine):
    body = {"source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01", "supplier_id": "SUP-B", "quantity": 78, "reason": "MySQL 合成并发审批验证", "draft_creation_confirmed": True}
    confirmation = {"actor": "MySQL race verification", "comment": "显式确认并发合成验证", "confirmed": True}
    def create():
        response = client.post("/purchase-requests/draft", json=body)
        assert response.status_code == 201
        request_id = response.json()["id"]
        assert client.post(f"/purchase-requests/{request_id}/submit", json=confirmation).status_code == 200
        return request_id
    decision_id = create()
    def decision(value):
        with Session(engine) as session:
            try:
                return decide(decision_id, Decision(**confirmation, decision=value), session).request.status
            except HTTPException as error:
                assert error.status_code == 409
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decision, ["APPROVED", "REJECTED"]))
    assert outcomes.count("CONFLICT") == 1
    detail = client.get(f"/purchase-requests/{decision_id}").json()
    assert detail["action"] is None and len(detail["events"]) == 2
    execute_id = create()
    assert client.post(f"/purchase-requests/{execute_id}/decision", json={**confirmation, "decision": "APPROVED"}).status_code == 200
    def execute(_):
        with Session(engine) as session:
            try:
                return mock_action(execute_id, Confirmation(**confirmation), session).action.reference
            except HTTPException as error:
                assert error.status_code == 409
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, range(2)))
    assert outcomes.count("CONFLICT") == 1
    detail = client.get(f"/purchase-requests/{execute_id}").json()
    assert detail["action"] is not None and len(detail["events"]) == 3
    return {"decision_one_winner": True, "execution_one_winner": True, "request_ids": [decision_id, execute_id]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Permit local migration, four synthetic drafts, approval records and two Mock receipts")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required")
    # Never honor a remote DATABASE_URL for this verification helper.
    url = URL.create("mysql+pymysql", username=os.getenv("MYSQL_USER", "procurepilot"), password=os.getenv("MYSQL_PASSWORD", "procurepilot_dev"), host="127.0.0.1", port=int(os.getenv("MYSQL_PORT", "3307")), database=os.getenv("MYSQL_DATABASE", "procurepilot"), query={"charset": "utf8mb4"})
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_engine.cache_clear()
    engine = get_engine()
    try:
        with engine.connect() as connection:
            legacy = [dict(row) for row in connection.execute(text("SELECT id,source_order_id,sku,supplier_id,quantity,reason,status,created_at FROM purchase_requests ORDER BY id")).mappings()]
        snapshot = PROJECT_ROOT / ".local/stage5-pre-migration-requests.json"
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(legacy, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT VERSION()"))
            preserved = [dict(row) for row in connection.execute(text("SELECT id,source_order_id,sku,supplier_id,quantity,reason,status,created_at FROM purchase_requests ORDER BY id")).mappings()]
        if legacy != preserved:
            raise ValueError("Migration changed existing purchase request data")
        with TestClient(app) as client:
            def call(path, body=None):
                response = client.get(path) if body is None else client.post(path, json=body)
                return response.status_code, response.json()
            report = {**run_demo(call), "database": "mysql", "version": version, "migration": "0002_approval", "model_consistency": True, "existing_requests_preserved": len(legacy)}
            report["concurrency"] = concurrency_check(client, engine)
    except Exception as error:
        report = {"passed": False, "database": "mysql", "error_type": type(error).__name__}
    finally:
        engine.dispose()
    output = PROJECT_ROOT / ".local/stage5-mysql-result.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
