"""Human approval HTTP demo. --confirm permits two synthetic drafts and local mock records."""
import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def run_demo(call):
    body = {"source_order_id": "PO-2026-10086", "sku": "SKU-MOTOR-01", "supplier_id": "SUP-B", "quantity": 78, "reason": "Stage 5 合成人工审批演示", "draft_creation_confirmed": True}
    confirmation = {"actor": "Stage 5 Demo", "comment": "显式确认的合成演示", "confirmed": True}
    before_stock = call("/inventory/SKU-MOTOR-01")[1]
    before_order = call("/orders/PO-2026-10086")[1]
    ids = []
    for decision in ["REJECTED", "APPROVED"]:
        code, draft = call("/purchase-requests/draft", body)
        assert code == 201 and draft["status"] == "DRAFT"
        request_id = draft["id"]
        ids.append(request_id)
        path = f"/purchase-requests/{request_id}"
        assert call(path + "/mock-action", confirmation)[0] == 409
        assert call(path + "/submit", confirmation)[1]["request"]["status"] == "WAITING_APPROVAL"
        assert call(path + "/mock-action", confirmation)[0] == 409
        result = call(path + "/decision", {**confirmation, "decision": decision})[1]
        assert result["request"]["status"] == decision and result["action"] is None
        if decision == "REJECTED":
            assert call(path + "/mock-action", confirmation)[0] == 409
            assert call(path + "/decision", {**confirmation, "decision": "APPROVED"})[0] == 409
            detail = call(path)[1]
            assert detail["action"] is None and [event["event"] for event in detail["events"]] == ["SUBMITTED", "REJECTED"]
        else:
            assert call(path + "/mock-action", {**confirmation, "confirmed": False})[0] == 403
            result = call(path + "/mock-action", confirmation)[1]
            assert result["action"]["reference"].startswith("MOCK-")
            assert call(path + "/mock-action", confirmation)[0] == 409
            assert len(call(path)[1]["events"]) == 3
    assert call("/inventory/SKU-MOTOR-01")[1] == before_stock
    assert call("/orders/PO-2026-10086")[1] == before_order
    return {"passed": True, "stage": 5, "request_ids": ids, "checks": ["rejection_never_executes", "approval_requires_separate_confirmation", "duplicate_execution_blocked", "atomic_audit_and_mock_receipt", "stock_and_order_unchanged"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required to create synthetic approval / mock records on local port 8000")
    def call(path, body=None):
        request = Request("http://127.0.0.1:8000" + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    try:
        report = run_demo(call)
    except (AssertionError, KeyError, URLError, OSError) as error:
        report = {"passed": False, "stage": 5, "error_type": type(error).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
