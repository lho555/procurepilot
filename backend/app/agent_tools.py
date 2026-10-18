"""Read-only business tool boundary; each call owns and closes its Session."""
from collections.abc import Callable
from typing import Protocol

import anyio
from fastapi import HTTPException
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.knowledge import KnowledgeError, KnowledgeStore
from app.knowledge_routes import KnowledgeQuestion, ask
from app.routes import get_inventory, get_order, get_supplier
from app.schemas import InventoryResponse, OrderResponse, SupplierResponse

READ_TOOLS = frozenset({"get_purchase_order", "get_inventory", "get_supplier", "retrieve_policy"})


class ToolFailure(Exception):
    pass


class RecordNotFound(ToolFailure):
    pass


class TransientToolFailure(ToolFailure):
    pass


class ToolClient(Protocol):
    async def call(self, name: str, arguments: dict) -> dict: ...


class ReadTools:
    def __init__(self, engine: Engine, store: KnowledgeStore):
        self.engine = engine
        self.store = store
        self.limiter = anyio.CapacityLimiter(4)

    def _business_read(self, operation: Callable, schema, key: str) -> dict:
        try:
            with Session(self.engine) as session:
                return schema.model_validate(operation(key, session)).model_dump(mode="json")
        except HTTPException as error:
            raise RecordNotFound("业务记录不存在") from error
        except OperationalError as error:
            code = error.orig.args[0] if getattr(error.orig, "args", None) else None
            if code in {2002, 2003, 2006, 2013} or "locked" in str(code).lower():
                raise TransientToolFailure("数据库暂时不可用") from error
            raise ToolFailure("数据库读取失败，请检查连接和迁移") from error
        except SQLAlchemyError as error:
            raise ToolFailure("数据库读取失败，请检查连接和迁移") from error

    def _call(self, name: str, arguments: dict) -> dict:
        if name == "get_purchase_order":
            return self._business_read(get_order, OrderResponse, arguments["order_id"])
        if name == "get_inventory":
            return self._business_read(get_inventory, InventoryResponse, arguments["sku"])
        if name == "get_supplier":
            return self._business_read(get_supplier, SupplierResponse, arguments["supplier_id"])
        if name == "retrieve_policy":
            try:
                return ask(KnowledgeQuestion(question=arguments["question"], top_k=3), self.store).model_dump(mode="json")
            except KnowledgeError as error:
                raise ToolFailure("知识索引不可用，请先完成文档导入") from error
        raise ToolFailure("工作流不允许调用该工具")

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in READ_TOOLS:
            raise ToolFailure("工作流只允许读取工具")
        # Cancellation stops waiting, not the underlying synchronous SELECT.
        # Workers only read and cannot publish a late result back into graph state.
        return await anyio.to_thread.run_sync(lambda: self._call(name, arguments), abandon_on_cancel=True, limiter=self.limiter)
