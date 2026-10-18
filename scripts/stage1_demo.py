"""Run the Stage 1 read workflow against a running API (or Vite proxy)."""
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--confirm-draft-write", action="store_true", help="Explicitly approve saving one synthetic DRAFT; repeating this creates another draft")
    args = parser.parse_args()
    checks = []

    def call(path, data=None):
        request = Request(
            args.api.rstrip("/") + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    try:
        order = call("/orders/PO-2026-10086")
        stock = call("/inventory/SKU-MOTOR-01")
        supplier = call(f"/suppliers/{order['supplier_id']}")
        alternative = call("/suppliers/SUP-B")
        quantity = next(item["quantity"] for item in order["items"] if item["sku"] == stock["sku"])
        shortage = max(quantity - stock["available"], 0)
        if shortage != 78 or order["supplier_delivery_date"] <= order["due_date"]:
            raise ValueError("Synthetic scenario changed: expected shortage 78 and delayed delivery")
        checks.append({"check": "order_inventory_supplier", "status": "pass", "shortage": shortage, "supplier": supplier["id"], "alternative": alternative["id"]})
        normal = call("/orders/PO-2026-10087")
        normal_stock = call("/inventory/SKU-BOLT-01")
        if normal_stock["available"] < normal["items"][0]["quantity"] or normal["supplier_delivery_date"] > normal["due_date"]:
            raise ValueError("Expected normal order to have enough stock and on-time delivery")
        checks.append({"check": "normal_order", "status": "pass"})
        if args.confirm_draft_write:
            draft = call("/purchase-requests/draft", {
                "source_order_id": order["id"], "sku": stock["sku"], "supplier_id": alternative["id"],
                "quantity": shortage, "reason": "Stage 1 合成 Demo：确认保存补货草稿", "draft_creation_confirmed": True,
            })
            if draft["status"] != "DRAFT" or call("/inventory/SKU-MOTOR-01") != stock:
                raise ValueError("Draft must remain DRAFT and not modify inventory")
            checks.append({"check": "confirmed_draft", "status": "pass", "id": draft["id"], "state": draft["status"]})
    except (HTTPError, URLError, OSError, ValueError, KeyError, StopIteration) as error:
        print(json.dumps({"stage": 1, "passed": False, "checks": checks, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"stage": 1, "passed": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
