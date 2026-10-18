"""Independent stdio server. Protocol output goes to stdout; no ERP writes without confirmation."""
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ValidationError, TypeAdapter
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.routes import get_order, get_inventory as read_inventory, get_supplier as read_supplier, create_draft
from app.schemas import Identifier, OrderResponse, InventoryResponse, SupplierResponse, DraftInput

server = FastMCP("ProcurePilot synthetic ERP", log_level="WARNING")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def execute(operation):
    try:
        with Session(get_engine()) as session:
            return {"ok": True, "data": operation(session)}
    except ValidationError:
        return {"ok": False, "code": "INVALID_INPUT"}
    except HTTPException as error:
        return {"ok": False, "code": {404: "NOT_FOUND", 403: "CONFIRMATION_REQUIRED", 409: "CONFLICT"}.get(error.status_code, "INVALID_INPUT")}
    except OperationalError as error:
        code = error.orig.args[0] if getattr(error.orig, "args", None) else None
        transient = code in {2002, 2003, 2006, 2013} or "locked" in str(code).lower()
        return {"ok": False, "code": "TRANSIENT" if transient else "DATABASE_ERROR"}
    except (SQLAlchemyError, ValueError):
        return {"ok": False, "code": "DATABASE_ERROR"}


def read(operation, schema, key):
    return execute(lambda session: schema.model_validate(operation(TypeAdapter(Identifier).validate_python(key), session)).model_dump(mode="json"))


@server.tool(annotations=READ)
def get_purchase_order(order_id: str) -> dict[str, Any]:
    """Read a synthetic purchase order and its items."""
    return read(get_order, OrderResponse, order_id)


@server.tool(annotations=READ)
def get_inventory(sku: str) -> dict[str, Any]:
    """Read inventory; never reserve or deduct stock."""
    return read(read_inventory, InventoryResponse, sku)


@server.tool(annotations=READ)
def get_supplier(supplier_id: str) -> dict[str, Any]:
    """Read a supplier and its quotes."""
    return read(read_supplier, SupplierResponse, supplier_id)


@server.tool(annotations=WRITE)
def create_purchase_request_draft(body: dict) -> dict[str, Any]:
    """Save only DRAFT after explicit draft_creation_confirmed=true. No automatic retries; no purchasing execution."""
    # Validate raw values here: MCP schema coercion must not turn strings/integers into human confirmation.
    return execute(lambda session: create_draft(DraftInput.model_validate(body), session).model_dump(mode="json"))


if __name__ == "__main__":
    server.run(transport="stdio")
