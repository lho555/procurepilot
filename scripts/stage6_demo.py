"""Own two local API processes to prove checkpoint recovery after a real restart."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import build_engine
from app.models import MockAction, PurchaseRequest
from app.seed import seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Permit local migration, synthetic seed, two workflows and one Mock receipt")
    parser.add_argument("--database", choices=["sqlite", "mysql"], default="sqlite")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required for local database writes")
    local = PROJECT_ROOT / ".local"
    local.mkdir(exist_ok=True)
    url = URL.create("sqlite", database=str(local / "stage6-demo.db")) if args.database == "sqlite" else URL.create("mysql+pymysql", username=os.getenv("MYSQL_USER", "procurepilot"), password=os.getenv("MYSQL_PASSWORD", "procurepilot_dev"), host="127.0.0.1", port=int(os.getenv("MYSQL_PORT", "3307")), database=os.getenv("MYSQL_DATABASE", "procurepilot"), query={"charset": "utf8mb4"})
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    engine = build_engine(url)
    process = None
    log = (local / f"stage6-{args.database}-api.log").open("a", encoding="utf-8")
    port = 8016
    opener = build_opener(ProxyHandler({}))  # Loopback verification must not pass through a system proxy.
    def call(path, body=None):
        request = Request(f"http://127.0.0.1:{port}" + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with opener.open(request, timeout=45) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    def stop():
        nonlocal process
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            process = None
    def launch():
        nonlocal process
        instance = uuid4().hex
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(port), "--header", f"x-demo-instance:{instance}"], cwd=PROJECT_ROOT, env=dict(os.environ), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Owned API failed to launch (port may be occupied); no existing process will be stopped")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status == 200 and response.headers.get("x-demo-instance") == instance:
                        return
            except (URLError, OSError):
                pass
            time.sleep(0.1)
        raise RuntimeError("Owned API startup timed out")
    report = {"passed": False, "stage": 6, "database": args.database}
    try:
        config = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        with Session(engine) as session, session.begin():
            seed(session)
        with Session(engine) as session:
            before_requests = session.scalar(select(func.count()).select_from(PurchaseRequest))
            before_actions = session.scalar(select(func.count()).select_from(MockAction))
        launch()
        body = {"idempotency_key": f"demo-{uuid4().hex}", "query": "分析 PO-2026-10086", "actor": "Stage 6 Demo", "comment": "确认保存草稿并提交审批", "confirmed": True,
                "draft": {"source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01", "supplier_id": "SUP-B", "quantity": 78, "reason": "Stage 6 合成恢复验证", "draft_creation_confirmed": True}}
        confirmation = {"actor": "Stage 6 Demo", "comment": "确认恢复合成工作流", "confirmed": True}
        stock = call("/inventory/SKU-MOTOR-01")[1]
        code, waiting = call("/workflows", body)
        assert code == 200 and waiting["status"] == "WAITING_APPROVAL", waiting
        run_id, request_id = waiting["id"], waiting["request_id"]
        trace = waiting["state"]["analysis"]
        first_pid = process.pid
        stop()
        launch()
        second_pid = process.pid
        recovered = call(f"/workflows/{run_id}/resume", confirmation)[1]
        assert recovered["status"] == "WAITING_APPROVAL" and recovered["revision"] == waiting["revision"]
        assert recovered["state"]["analysis"] == trace
        assert call(f"/purchase-requests/{request_id}/decision", {**confirmation, "decision": "APPROVED"})[0] == 200
        ready = call(f"/workflows/{run_id}/resume", confirmation)[1]
        assert ready["status"] == "WAITING_EXECUTION" and ready["request"]["action"] is None
        completed = call(f"/workflows/{run_id}/resume", {**confirmation, "execute_confirmed": True})[1]
        assert completed["status"] == "COMPLETED" and completed["state"]["analysis"] == trace
        assert call("/workflows", body)[1] == completed
        assert call(f"/workflows/{run_id}/resume", {**confirmation, "execute_confirmed": True})[1] == completed
        assert call("/workflows", {**body, "comment": "different"})[0] == 409
        rejected = call("/workflows", {**body, "idempotency_key": f"reject-{uuid4().hex}"})[1]
        assert rejected["status"] == "WAITING_APPROVAL"
        assert call(f"/purchase-requests/{rejected['request_id']}/decision", {**confirmation, "decision": "REJECTED"})[0] == 200
        assert call(f"/workflows/{rejected['id']}/resume", {**confirmation, "execute_confirmed": True})[1]["status"] == "REJECTED"
        assert call("/inventory/SKU-MOTOR-01")[1] == stock
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(PurchaseRequest)) == before_requests + 2
            assert session.scalar(select(func.count()).select_from(MockAction)) == before_actions + 1
        report = {"passed": True, "stage": 6, "database": args.database, "process_ids": [first_pid, second_pid], "run_id": run_id, "request_id": request_id, "analysis_run_id": trace["run_id"], "checkpoints": completed["checkpoints"],
                  "checks": ["actual_api_restart", "saved_analysis_unchanged", "one_draft_per_workflow", "explicit_execution_confirmation", "idempotent_start_and_resume", "key_conflict_rejected", "rejection_no_action", "stock_unchanged"]}
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        stop(); log.close(); engine.dispose()
    (local / f"stage6-{args.database}-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
