"""Run fixed Golden Tasks against an isolated real API and stdio MCP server."""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
import platform
from pathlib import Path
from time import monotonic
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import build_engine
from app.knowledge import build_index, save_index
from app.models import InventoryItem, MockAction, PurchaseOrder, PurchaseRequest
from app.seed import seed
from evals.scorer import aggregate, citations_valid, markdown_report, score_analysis
from evals.server import LocalAPI

DRAFT = {"source_order_id":"PO-2026-10086","sku":"SKU-MOTOR-01","supplier_id":"SUP-B","quantity":78,"reason":"Stage 7 合成评测","draft_creation_confirmed":True}
CONFIRM = {"actor":"Stage 7 Eval","comment":"显式确认的隔离合成评测","confirmed":True}
KINDS = {"analysis", "knowledge", "draft", "approval_required", "approval_rejected", "recovery", "idempotency"}


def applicable_metrics(task):
    names = []
    if task["kind"] == "analysis":
        names += ["tool_selection_correct", "tool_arguments_correct"]
        if task["expected"]["status"] == "COMPLETED":
            names += ["policy_citation_correct"]
    if task["kind"] == "knowledge":
        names += ["policy_citation_correct"]
    if task["kind"] in {"approval_required", "approval_rejected", "recovery"} or (task["kind"] == "draft" and "confirmation" in task["id"]):
        names += ["approval_required_correct"]
    if task["kind"] == "recovery":
        names += ["recovery_success"]
    return names


def load_tasks(path):
    raw = path.read_bytes()
    if len(raw) > 100_000:
        raise ValueError("Golden task file too large")
    data = json.loads(raw)
    tasks = data.get("tasks", [])
    if data.get("version") != 1 or not 10 <= len(tasks) <= 100:
        raise ValueError("Need version 1 and 10..100 tasks")
    if any(not isinstance(task, dict) for task in tasks):
        raise ValueError("Tasks must be objects")
    ids = [task.get("id") for task in tasks]
    if any(not isinstance(key, str) or not key.replace('_','').isalnum() for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid or duplicate task ids")
    if any(task.get("kind") not in KINDS or not isinstance(task.get("expected"), dict) for task in tasks):
        raise ValueError("Invalid kind or expected outcome")
    return tasks, hashlib.sha256(raw).hexdigest()


def snapshot(engine):
    with Session(engine) as session:
        return {"drafts":session.scalar(select(func.count()).select_from(PurchaseRequest)),
                "actions":session.scalar(select(func.count()).select_from(MockAction)),
                "stock":[(row.sku,row.on_hand,row.reserved) for row in session.scalars(select(InventoryItem).order_by(InventoryItem.sku))],
                "orders":[(row.id,str(row.due_date),str(row.supplier_delivery_date),row.status) for row in session.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id))]}


def evaluate(task, api, documents):
    kind, expected = task["kind"], task["expected"]
    checks, metrics, evidence = {}, {}, {}
    def call(path, body=None):
        code, result = api.call(path, body)
        evidence.setdefault("responses", []).append({"path":path,"http":code,"body":result})
        return code, result
    def check(name, passed):
        checks[name] = bool(passed)
    def draft_matches(record):
        return all(record.get(key) == value for key, value in DRAFT.items() if key != "draft_creation_confirmed")
    if kind == "analysis":
        code, result = call("/agent/analyze", {"query":task["query"]})
        checks, metrics = score_analysis(result, expected)
        check("http", code == 200)
        if expected["status"] == "COMPLETED":
            valid = citations_valid(result.get("policy",{}), result.get("final_answer",""), documents)
            check("citations", valid); metrics["policy_citation_correct"] = valid
    elif kind == "knowledge":
        code, result = call("/knowledge/ask", {"question":task["query"],"top_k":3})
        check("http", code == 200)
        check("found", result.get("found") is expected["found"])
        check("answer", all(text in result.get("answer", "") for text in expected["contains"]))
        valid = citations_valid(result, result.get("answer",""), documents) if expected["found"] else result.get("found") is False and result.get("citations") == []
        if expected["found"]:
            check("expected_source", any(item.get("filename") == expected["filename"] for item in result.get("citations",[])))
        check("citations", valid); metrics["policy_citation_correct"] = valid
    elif kind == "draft":
        code, _ = call("/purchase-requests/draft", {**DRAFT,**task["changes"]})
        check("rejected_input", code == expected["http"])
        if "confirmation" in task["id"]:
            metrics["approval_required_correct"] = checks["rejected_input"]
    elif kind in {"approval_required","approval_rejected"}:
        code, draft = call("/purchase-requests/draft", DRAFT)
        check("draft_created", code == 201)
        path = f"/purchase-requests/{draft['id']}"
        check("draft_cannot_execute", call(path+"/mock-action", CONFIRM)[0] == 409)
        check("submitted", call(path+"/submit", CONFIRM)[1]["request"]["status"] == "WAITING_APPROVAL")
        check("waiting_cannot_execute", call(path+"/mock-action", CONFIRM)[0] == 409)
        decision = "REJECTED" if kind == "approval_rejected" else "APPROVED"
        code, result = call(path+"/decision", {**CONFIRM,"decision":decision})
        check("decision", code == 200 and result["request"]["status"] == expected["status"] and result["action"] is None)
        if kind == "approval_rejected":
            check("rejected_cannot_execute", call(path+"/mock-action", CONFIRM)[0] == 409)
            check("rejected_cannot_approve", call(path+"/decision", {**CONFIRM,"decision":"APPROVED"})[0] == 409)
        else:
            check("approved_still_needs_confirmation", call(path+"/mock-action", {**CONFIRM,"confirmed":False})[0] == 403)
        detail = call(path)[1]
        check("draft_parameters", draft_matches(detail["request"]))
        check("no_action_and_audit", detail["action"] is None and [row["event"] for row in detail["events"]] == ["SUBMITTED",decision])
        metrics["approval_required_correct"] = all(checks.values())
    elif kind in {"recovery","idempotency"}:
        body = {**CONFIRM,"query":"分析 PO-2026-10086","idempotency_key":f"eval-{task['id']}-{uuid4().hex}","draft":DRAFT}
        code, waiting = call("/workflows", body)
        check("waiting", code == 200 and waiting["status"] == "WAITING_APPROVAL")
        check("draft_parameters", draft_matches(waiting["request"]["request"]))
        run_id, request_id = waiting["id"], waiting["request_id"]
        if kind == "recovery":
            old_pid = api.process.pid
            api.restart()
            evidence["restart_process_ids"] = [old_pid,api.process.pid]
            recovered = call(f"/workflows/{run_id}/resume", CONFIRM)[1]
            check("actual_restart", old_pid != api.process.pid)
            check("saved_analysis_and_draft", recovered["state"]["analysis"] == waiting["state"]["analysis"] and recovered["request_id"] == request_id and recovered["revision"] == waiting["revision"])
            check("approved", call(f"/purchase-requests/{request_id}/decision", {**CONFIRM,"decision":"APPROVED"})[0] == 200)
            ready = call(f"/workflows/{run_id}/resume", CONFIRM)[1]
            check("separate_confirmation", ready["status"] == "WAITING_EXECUTION" and ready["request"]["action"] is None)
            done = call(f"/workflows/{run_id}/resume", {**CONFIRM,"execute_confirmed":True})[1]
            check("completed", done["status"] == expected["status"] and done["request"]["action"] is not None)
            replay = call(f"/workflows/{run_id}/resume", {**CONFIRM,"execute_confirmed":True})[1]
            check("no_repeat_action", replay == done)
            check("no_repeat_analysis", done["state"]["analysis"] == waiting["state"]["analysis"])
            metrics["recovery_success"] = all(checks.values())
            metrics["approval_required_correct"] = checks["separate_confirmation"]
        else:
            replay = call("/workflows", body)[1]
            check("same_run_snapshot", replay == waiting)
            check("conflicting_input", call("/workflows", {**body,"comment":"different"})[0] == 409)
            check("terminal_target", replay["status"] == expected["status"])
    return checks, metrics, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Approve only isolated synthetic evaluation database / API / report writes")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required; existing databases are never used")
    task_path = PROJECT_ROOT / "evals/golden_tasks.json"
    tasks, digest = load_tasks(task_path)
    directory = PROJECT_ROOT / ".local/evals" / uuid4().hex
    directory.mkdir(parents=True)
    url = f"sqlite:///{(directory/'eval.db').as_posix()}"
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    engine, api = build_engine(url), None
    results = []
    report = {"stage":7,"adapter":"mock","retrieval":"tfidf-char-ngram-v1","transport":"real stdio MCP","database":"isolated SQLite","golden_sha256":digest,
              "environment":{"python":platform.python_version(),"platform":platform.platform()},
              "versions":{name:version(name) for name in ["langgraph","mcp","fastapi"]},"scope":"deterministic synthetic workflow; not autonomous LLM or semantic reasoning quality",
              "started_at":datetime.now(timezone.utc).isoformat()}
    try:
        command.upgrade(Config(str(PROJECT_ROOT/"backend/alembic.ini")), "head")
        with Session(engine) as session, session.begin():
            seed(session)
        index = build_index(PROJECT_ROOT/"sample_data/policies")
        save_index(index, directory/"knowledge.json")
        documents = {doc.id:doc.model_dump() for doc in index.documents}
        report["knowledge_revision"] = index.revision
        api = LocalAPI(directory, url, directory/"knowledge.json")
        api.start()
        for task in tasks:
            started = monotonic()
            before = snapshot(engine)
            try:
                checks, observed_metrics, evidence = evaluate(task, api, documents)
                metrics = {**dict.fromkeys(applicable_metrics(task), False), **observed_metrics}
                error = None
            except Exception as failure:
                checks, metrics, evidence = {"execution":False}, dict.fromkeys(applicable_metrics(task), False), {}
                # Sanitize error type; full local API traceback is in isolated api.log.
                error = type(failure).__name__
            after = snapshot(engine)
            expected = task["expected"]
            checks["authorized_effects"] = (after["drafts"]-before["drafts"] == expected.get("drafts",0) and after["actions"]-before["actions"] == expected.get("actions",0) and after["stock"] == before["stock"] and after["orders"] == before["orders"])
            metrics["unauthorized_action_rate"] = not checks["authorized_effects"]
            for metric in ["approval_required_correct", "recovery_success"]:
                if metric in metrics:
                    metrics[metric] = metrics[metric] and checks["authorized_effects"]
            metrics["task_success"] = all(checks.values())
            row = {"id":task["id"],"kind":task["kind"],"expected":expected,"passed":metrics["task_success"],"checks":checks,"metrics":metrics,"error_type":error,
                   "duration_ms":round((monotonic()-started)*1000,2),"effects":{"drafts":after["drafts"]-before["drafts"],"actions":after["actions"]-before["actions"]}}
            evidence_path = directory / f"{task['id']}.json"
            evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
            row["evidence"] = str(evidence_path.relative_to(PROJECT_ROOT))
            results.append(row)
    except Exception as failure:
        report["setup_error_type"] = type(failure).__name__
    finally:
        if api:
            api.close()
        engine.dispose()
        if previous_url is None:
            os.environ.pop("DATABASE_URL",None)
        else:
            os.environ["DATABASE_URL"] = previous_url
    report["results"] = results
    report["task_count"] = len(tasks)
    report["evaluated_count"] = len(results)
    report["metrics"] = aggregate(results)
    report["passed"] = len(results) == len(tasks) and all(row["passed"] for row in results) and "setup_error_type" not in report
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path = directory / "report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (directory / "report.md").write_text(markdown_report(report),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
