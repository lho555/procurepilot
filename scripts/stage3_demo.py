"""Verify the bounded read-only agent workflow against a running API."""
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    checks = []

    def call(path, body=None):
        request = Request(args.api.rstrip("/") + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        with urlopen(request, timeout=35) as response:
            return json.load(response)

    try:
        before = call("/inventory/SKU-MOTOR-01")
        result = call("/agent/analyze", {"query": "PO-2026-10086 为什么缺货和延期？追加采购先让我审批。"})
        if result["status"] != "COMPLETED" or not result["read_only"] or result["adapter"] != "mock":
            raise ValueError("Expected completed mock read-only workflow")
        if result["facts"]["shortages"][0]["shortage"] != 78 or result["facts"]["delay_days"] != 2:
            raise ValueError("Expected shortage 78 and delay 2 days")
        tools = {entry["tool"] for entry in result["tool_calls"]}
        if tools != {"get_purchase_order", "get_inventory", "get_supplier", "retrieve_policy"}:
            raise ValueError("Expected the four read-only tools")
        if not result["policy"]["citations"] or call("/inventory/SKU-MOTOR-01") != before:
            raise ValueError("Missing citations or changed inventory")
        checks.append({"check": "shortage_delay_tools_citations", "status": "pass", "run_id": result["run_id"]})
        normal = call("/agent/analyze", {"query": "分析 PO-2026-10087"})
        if normal["status"] != "COMPLETED" or normal["facts"]["needs_review"]:
            raise ValueError("Expected normal order without shortage/delay review")
        checks.append({"check": "normal_order", "status": "pass"})
        missing = call("/agent/analyze", {"query": "请分析订单问题"})
        if missing["status"] != "NEEDS_INPUT" or missing["tool_calls"]:
            raise ValueError("Missing order ID must not query tools")
        absent = call("/agent/analyze", {"query": "分析 PO-2026-99999"})
        if absent["status"] != "NOT_FOUND" or len(absent["tool_calls"]) != 1:
            raise ValueError("Missing order must not retry or continue")
        checks.append({"check": "missing_input_and_not_found", "status": "pass"})
    except (HTTPError, URLError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"stage": 3, "passed": False, "checks": checks, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"stage": 3, "passed": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
