"""Offline adapter: no model/API key/tool execution, only grounded formatting."""
from typing import Protocol


class LLMAdapter(Protocol):
    name: str

    async def summarize(self, facts: dict, policy: dict) -> str: ...


class MockLLMAdapter:
    name = "mock"

    async def summarize(self, facts: dict, policy: dict) -> str:
        order = facts["order"]
        summary = [f"订单 {order['id']} 的只读分析（合成快照）："]
        for item in facts["shortages"]:
            summary.append(f"{item['sku']}：需求 {item['required']}，可用库存 {item['available']}，参考缺口 {item['shortage']}。")
        if facts["delay_days"] > 0:
            summary.append(f"供应商预计交付比要求交期晚 {facts['delay_days']} 天。")
        else:
            summary.append("供应商预计交付未晚于要求交期。")
        if facts["needs_review"]:
            summary.append("建议人工核实库存与交期，查询替代供应商报价和起订量；如需追加采购，先准备草稿并按制度提交人工审批。")
        else:
            summary.append("当前快照未显示缺货或延期，不据此新增采购；继续人工核实实际库存和交期。")
        if policy["found"]:
            summary.append("制度依据仅摘录以下导入快照，不代表采购已获批准：")
            summary.extend(f"[{citation['number']}] {citation['excerpt']}" for citation in policy["citations"])
        else:
            summary.append("未找到制度依据，请交由人工核实；本次分析不作审批判断。")
        summary.append("本次没有保存草稿、修改库存、下单或执行采购。")
        return "\n\n".join(summary)
