"""Explicitly seed synthetic demo records without overwriting existing data."""
import argparse
from datetime import date
from decimal import Decimal
import json
import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_engine
from app.models import InventoryItem, PurchaseOrder, PurchaseOrderItem, Supplier, SupplierQuote


def seed(session: Session) -> dict[str, int]:
    data = json.loads((PROJECT_ROOT / "sample_data/stage1.json").read_text(encoding="utf-8"))
    inserted = skipped = 0
    for table, model in (
        ("suppliers", Supplier), ("inventory_items", InventoryItem),
        ("supplier_quotes", SupplierQuote), ("purchase_orders", PurchaseOrder),
        ("purchase_order_items", PurchaseOrderItem),
    ):
        for values in data[table]:
            if model is PurchaseOrder:
                values["due_date"] = date.fromisoformat(values["due_date"])
                values["supplier_delivery_date"] = date.fromisoformat(values["supplier_delivery_date"])
            if model is SupplierQuote:
                values["unit_price"] = Decimal(values["unit_price"])
            key = ((values["order_id"], values["sku"]) if model is PurchaseOrderItem
                   else values["sku"] if model is InventoryItem else values["id"])
            if session.get(model, key) is not None:
                skipped += 1
            else:
                session.add(model(**values))
                inserted += 1
        session.flush()
    return {"inserted": inserted, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Confirm inserting synthetic data into the configured local database")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required for this write action")
    try:
        with Session(get_engine()) as session, session.begin():
            result = seed(session)
    except SQLAlchemyError:
        print("Seed failed; transaction rolled back. Check the database and run Alembic upgrade first.", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
