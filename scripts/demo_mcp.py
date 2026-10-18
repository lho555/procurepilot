"""Read-only Stage 4 demo against the configured synthetic database and knowledge index."""
import asyncio
import json

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from app.agent import AgentRuntime
from app.knowledge import get_knowledge_store
from app.mcp_tools import MCPReadTools, server_parameters


async def main():
    async with stdio_client(server_parameters()) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
    result = await AgentRuntime(MCPReadTools(get_knowledge_store())).run("PO-2026-10086 为什么无法按期交付？")
    calls = [call for call in result["tool_calls"] if call["transport"] == "mcp:stdio"]
    passed = result["status"] == "COMPLETED" and {call["tool"] for call in calls} == {"get_purchase_order", "get_inventory", "get_supplier"}
    print(json.dumps({"passed": passed, "protocol": initialized.protocolVersion, "server": initialized.serverInfo.name, "discovered_tools": names, "result": result}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
