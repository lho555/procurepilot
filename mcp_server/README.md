# mcp_server

Stage 4 独立 stdio ERP Server：`server.py` 提供三个读取和一个显式确认 DRAFT 工具。
从项目根目录设置 `PYTHONPATH=backend`，运行 `scripts/demo_mcp.py`；客户端自动启动 / 关闭服务进程。
手动连接其他 MCP 客户端时使用项目 `.venv` Python、参数 `-m mcp_server.server`、项目根目录 cwd，
PYTHONPATH 需包含项目根目录和 backend（Windows 用分号连接）。stdout 仅承载协议，日志走 stderr。
数据库配置沿用本地 `.env` / DATABASE_URL / MYSQL_*；迁移和 seed 必须预先显式执行，Server 不自动执行。

读取：get_purchase_order(order_id)、get_inventory(sku)、get_supplier(supplier_id)。
写入：create_purchase_request_draft(body)，复用严格 DraftInput 校验；缺少 / false / 字符串确认均拒绝。
只保存 DRAFT，不修改库存 / 订单，不执行采购；调用者必须展示并取得人工确认，不得自动重试写入。
返回结构化 `{ok: true, data: ...}` 或 `{ok: false, code: ...}`；数据库异常不返回连接信息。
服务只用于可信本机合成演示，无远程认证 / 生产权限隔离或幂等保障。Agent 客户端只能使用读取工具。
