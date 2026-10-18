"""Fixed local MCP transport; no direct ERP repository access or write capability."""
import asyncio
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import PROJECT_ROOT
from app.agent_tools import RecordNotFound, ToolFailure, TransientToolFailure
from app.knowledge import KnowledgeError, KnowledgeStore
from app.knowledge_routes import KnowledgeQuestion, ask
from app.schemas import OrderResponse, InventoryResponse, SupplierResponse

ERP_SCHEMAS = {"get_purchase_order": OrderResponse, "get_inventory": InventoryResponse, "get_supplier": SupplierResponse}


def server_parameters() -> StdioServerParameters:
    # Only trusted local configuration crosses into the child; never forward unrelated API keys.
    env = {key: value for key, value in os.environ.items() if key in {"DATABASE_URL", "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")])
    env["PYTHONIOENCODING"] = "utf-8"
    return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(PROJECT_ROOT), env=env)


class MCPReadTools:
    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.limiter = anyio.CapacityLimiter(4)

    def transport(self, name: str) -> str:
        return "mcp:stdio" if name in ERP_SCHEMAS else "local"

    async def call(self, name: str, arguments: dict) -> dict:
        if name == "retrieve_policy":
            try:
                return await anyio.to_thread.run_sync(lambda: ask(KnowledgeQuestion(question=arguments["question"], top_k=3), self.store).model_dump(mode="json"), abandon_on_cancel=True, limiter=self.limiter)
            except KnowledgeError as error:
                raise ToolFailure("知识索引不可用，请先完成文档导入") from error
        if name not in ERP_SCHEMAS:
            raise ToolFailure("工作流只允许读取工具")
        try:
            # One session/process per call: context exit also cleans up failures and cancellation.
            async with self.limiter:
                async with stdio_client(server_parameters()) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments)
                        if result.isError or not isinstance(result.structuredContent, dict):
                            raise ToolFailure("MCP 工具响应无效")
                        payload = result.structuredContent
            if payload.get("ok") is not True:
                if payload.get("code") == "NOT_FOUND":
                    raise RecordNotFound("业务记录不存在")
                if payload.get("code") == "TRANSIENT":
                    raise TransientToolFailure("MCP 数据库暂时不可用")
                raise ToolFailure("MCP 工具不可用，请检查数据库和迁移")
            return ERP_SCHEMAS[name].model_validate(payload["data"]).model_dump(mode="json")
        except ToolFailure:
            raise
        except Exception as error:
            # SDK cleanup task groups may wrap a cancellation in a transport exception.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError() from error
            raise ToolFailure("MCP 连接或响应失败") from error
