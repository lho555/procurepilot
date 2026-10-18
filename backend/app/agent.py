"""Bounded LangGraph workflow with per-run state and local attempt traces."""
import asyncio
from datetime import date, datetime, timezone
import re
from time import monotonic
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from app.agent_tools import READ_TOOLS, ToolClient, RecordNotFound, ToolFailure, TransientToolFailure
from app.llm_adapter import LLMAdapter, MockLLMAdapter
from app.model_explanation import ModelFailure


class AgentState(TypedDict, total=False):
    run_id: str
    query: str
    order_id: str | None
    status: str
    current_step: str
    facts: dict
    policy: dict
    trace: list[dict]
    tool_calls: list[dict]
    final_answer: str
    error: str | None
    adapter: str
    read_only: bool


class AgentRuntime:
    def __init__(self, tools: ToolClient, adapter: LLMAdapter | None = None, *, step_timeout: float = 5, total_timeout: float = 25, max_attempts: int = 2, retry_delay: float = 0.1):
        self.tools = tools
        self.adapter = adapter or MockLLMAdapter()
        self.step_timeout = step_timeout
        self.total_timeout = total_timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay

    async def run(self, query: str) -> AgentState:
        # All mutable data belongs to this invocation, including failed/cancelled attempts.
        trace: list[dict] = []
        current: AgentState = {
            "run_id": f"RUN-{uuid4().hex}", "query": query, "order_id": None,
            "status": "RUNNING", "current_step": "identify_order", "facts": {}, "policy": {},
            "trace": [], "tool_calls": [], "final_answer": "", "error": None,
            "adapter": self.adapter.name, "read_only": True,
        }

        async def attempt(step: str, name: str, arguments: dict, operation, *, retry: bool = True, timeout: float | None = None):
            current["current_step"] = step
            attempts = self.max_attempts if retry else 1
            for number in range(1, attempts + 1):
                started = monotonic()
                entry = {"step": step, "tool": name if name in READ_TOOLS else None, "arguments": arguments, "attempt": number,
                         "started_at": datetime.now(timezone.utc).isoformat(), "status": "RUNNING", "duration_ms": 0, "error": None}
                entry["transport"] = self.adapter.name if name == "summarize" else getattr(self.tools, "transport", lambda _name: "local")(name)
                trace.append(entry)
                try:
                    async with asyncio.timeout(timeout if timeout is not None else self.step_timeout):
                        result = await operation()
                    entry["status"] = "SUCCESS"
                    return result
                except RecordNotFound:
                    entry.update(status="FAILED", error="业务记录不存在")
                    raise
                except (TransientToolFailure, TimeoutError) as error:
                    entry.update(status="TIMEOUT" if isinstance(error, TimeoutError) else "RETRY", error="读取超时" if isinstance(error, TimeoutError) else "暂时读取失败")
                    if number == attempts:
                        if not isinstance(error, TimeoutError):
                            entry["status"] = "FAILED"
                        raise
                    await asyncio.sleep(self.retry_delay)
                except asyncio.CancelledError:
                    entry.update(status="CANCELLED", error="运行已取消或达到总超时")
                    raise
                except ToolFailure:
                    entry.update(status="FAILED", error="工具不可用")
                    raise
                except ModelFailure as error:
                    entry.update(status="FAILED", error=str(error))
                    raise
                except Exception as error:
                    entry.update(status="FAILED", error="步骤执行失败")
                    raise ToolFailure("步骤执行失败") from error
                finally:
                    entry["duration_ms"] = round((monotonic() - started) * 1000, 2)

        def update(**values):
            return {**values, "trace": [dict(entry) for entry in trace]}

        async def identify(state: AgentState):
            started = monotonic()
            ids = sorted(set(re.findall(r"(?<![A-Za-z0-9_-])PO-\d{4}-[A-Za-z0-9]{1,24}(?![A-Za-z0-9_-])", state["query"], flags=re.IGNORECASE)))
            ids = sorted(set(value.upper() for value in ids))
            trace.append({"step": "identify_order", "tool": None, "arguments": {}, "attempt": 1, "started_at": datetime.now(timezone.utc).isoformat(),
                          "status": "SUCCESS" if len(ids) == 1 else "NEEDS_INPUT", "duration_ms": round((monotonic() - started) * 1000, 2), "error": None})
            if len(ids) != 1:
                return update(status="NEEDS_INPUT", current_step="identify_order", error="请提供一个明确的订单号，例如 PO-2026-10086。", final_answer="请提供一个明确的订单号，例如 PO-2026-10086。")
            return update(order_id=ids[0], current_step="identify_order")

        async def order(state: AgentState):
            args = {"order_id": state["order_id"]}
            result = await attempt("query_order", "get_purchase_order", args, lambda: self.tools.call("get_purchase_order", args))
            return update(facts={"order": result}, current_step="query_order")

        async def inventory(state: AgentState):
            records = []
            for item in state["facts"]["order"]["items"]:
                args = {"sku": item["sku"]}
                records.append(await attempt("query_inventory", "get_inventory", args, lambda args=args: self.tools.call("get_inventory", args)))
            return update(facts={**state["facts"], "inventory": records}, current_step="query_inventory")

        async def supplier(state: AgentState):
            args = {"supplier_id": state["facts"]["order"]["supplier_id"]}
            result = await attempt("query_supplier", "get_supplier", args, lambda: self.tools.call("get_supplier", args))
            return update(facts={**state["facts"], "supplier": result}, current_step="query_supplier")

        async def policy(state: AgentState):
            args = {"question": "采购制度 缺货 供应商延期 紧急采购 人工审批"}
            result = await attempt("retrieve_policy", "retrieve_policy", args, lambda: self.tools.call("retrieve_policy", args))
            return update(policy=result, current_step="retrieve_policy")

        async def reason(state: AgentState):
            order_data = state["facts"]["order"]
            stock = {record["sku"]: record for record in state["facts"]["inventory"]}
            shortages = [{"sku": item["sku"], "required": item["quantity"], "available": stock[item["sku"]]["available"],
                          "shortage": max(item["quantity"] - stock[item["sku"]]["available"], 0)} for item in order_data["items"]]
            delay_days = max((date.fromisoformat(order_data["supplier_delivery_date"]) - date.fromisoformat(order_data["due_date"])).days, 0)
            facts = {**state["facts"], "shortages": shortages, "delay_days": delay_days, "needs_review": delay_days > 0 or any(item["shortage"] > 0 for item in shortages)}
            current["facts"] = facts
            answer = await attempt("reason", "summarize", {}, lambda: self.adapter.summarize(facts, state["policy"]), retry=False, timeout=12)
            return update(facts=facts, final_answer=answer, current_step="reason")

        async def final(_state: AgentState):
            trace.append({"step": "final_response", "tool": None, "arguments": {}, "attempt": 1, "started_at": datetime.now(timezone.utc).isoformat(), "status": "SUCCESS", "duration_ms": 0, "error": None})
            return update(status="COMPLETED", current_step="final_response")

        graph = StateGraph(AgentState)
        nodes = [("identify_order", identify), ("query_order", order), ("query_inventory", inventory), ("query_supplier", supplier), ("retrieve_policy", policy), ("reason", reason), ("final_response", final)]
        for name, operation in nodes:
            graph.add_node(name, operation)
        graph.add_edge(START, "identify_order")
        graph.add_conditional_edges("identify_order", lambda state: "query_order" if state["status"] == "RUNNING" else END, {"query_order": "query_order", END: END})
        for (name, _), (following, _) in zip(nodes[1:], nodes[2:]):
            graph.add_edge(name, following)
        graph.add_edge("final_response", END)
        compiled = graph.compile()
        try:
            # Even if global LangSmith tracing is enabled, this demo never exports its run.
            with tracing_context(enabled=False):
                async with asyncio.timeout(self.total_timeout):
                    async for changes in compiled.astream(current, stream_mode="updates", config={"recursion_limit": 12}):
                        for values in changes.values():
                            current.update(values)
        except RecordNotFound:
            current.update(status="NOT_FOUND", error="业务记录不存在", final_answer="未找到订单或关联业务记录，请核对输入。")
        except TimeoutError:
            current.update(status="TIMED_OUT", error="分析达到超时限制，请稍后重新运行。", final_answer="分析超时，已停止后续步骤；未执行任何写动作。")
        except ModelFailure as error:
            current.update(status="FAILED", error=str(error), final_answer="模型解释未完成，未切换为离线成功结果；未执行任何写动作。")
        except ToolFailure as error:
            current.update(status="FAILED", error=str(error), final_answer="分析未完成，请检查数据库或知识索引后重新运行；未执行任何写动作。")
        current["trace"] = [dict(entry) for entry in trace]
        current["tool_calls"] = [dict(entry) for entry in trace if entry["tool"] in READ_TOOLS]
        return current
