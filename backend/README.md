# backend

Stage 0～8 FastAPI 主应用。`GET /health` 保持 Stage 0 原有存活检查格式，不依赖数据库或知识索引。
业务入口：订单、库存、供应商查询，以及经过显式确认的采购草稿写入。
模型由 SQLAlchemy 管理；建表必须通过 Alembic，启动服务不会自动建表或 seed。

从项目根目录启动：

```powershell
./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Smoke test：`./.venv/Scripts/python.exe -m pytest backend/tests -q --import-mode=importlib -o pythonpath=backend`

从根目录初始化数据库：

```powershell
./.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m app.seed --confirm
```

seed 同一事务插入缺失的合成记录，不清空或覆盖已有记录。日期为固定演示快照。
`DATABASE_URL` 可覆盖默认 MySQL；SQLite 用于本地演示和迁移/API 集成测试。
请求使用独立 Session；失败关闭 / 回滚事务，数据库错误返回不包含内部连接信息的 503。
创建草稿时固定 DRAFT，数量严格为正整数；Decimal 报价以字符串输出，草稿时间以 UTC 输出。

依赖安装、SQLite 回退、写入边界和跨平台命令见根目录 README。

Stage 2：`app.import_knowledge --confirm` 导入本地 Markdown，`app.knowledge` 使用本地 TF-IDF 向量和余弦检索，
`app.knowledge_routes` 提供制度查询及来源快照。回答严格摘录 chunk，不调用生成式模型。
原文、行号、哈希随索引持久化；临时文件写入完成后才原子替换，缓存根据文件变化重新加载。
查询和来源读取不依赖 MySQL；索引缺失 / 损坏返回 503。

Stage 4：`app.agent` 用 LangGraph 构建七个顺序节点；默认 `app.mcp_tools.MCPReadTools` 通过独立 stdio 进程读取 ERP，
制度读取为本地模块。`app.agent_tools.ReadTools` 保留用于图逻辑测试，不是默认运行路径。
Server 复用原有查询、响应模型和草稿校验；`app.llm_adapter` 提供离线 Mock 适配器。
`POST /agent/analyze` 返回运行状态、事实、引用和尝试记录，不创建采购草稿。
每次最多两次尝试 / 5 秒等待，总运行默认 25 秒；超时退出 MCP 会话并由 SDK 清理进程，清理时间可能延长响应。
State / trace 在本次请求中保留，不持久化；LangSmith 导出显式关闭，LangGraph 依赖自带 checkpoint 包不代表本项目已经实现恢复。

Stage 5：app.approval 提供申请列表 / 详情、submit、decision 和 mock-action。
用数据库条件更新限定 DRAFT → WAITING_APPROVAL → APPROVED / REJECTED；只有 APPROVED 且未执行可创建本地 Mock 回执。
更新、ApprovalEvent 和 MockAction 在同一事务内完成；冲突返回 409、未确认 403、非法输入 422。
每次操作要求严格布尔 confirmed 和非空 actor / comment；名称不是身份凭证，当前是本机合成演示。
0002_approval 扩展状态约束和两张表，保留已有草稿；不自动迁移。

Stage 6：app.workflow 使用应用层事务 checkpoint，将 LangGraph 完整分析 State 和下一业务阶段保存到 MySQL / SQLite。
运行创建幂等键、60 秒租约、所有者校验、草稿 / 审批提交和 checkpoint 原子写入；恢复不重复已保存分析或草稿。
save_draft / approval.change 的 commit=False 只由内部工作流使用，不能通过 API 参数开启；共用外层事务。
批准后仍需 execute_confirmed=true 才执行 Mock，拒绝恢复到终态；原 /agent/analyze 保持独立只读接口。
未实现 LangGraph BaseCheckpointSaver / 原生 interrupt；分析保存前的崩溃可能重做只读分析，已保存阶段不重做。
详情、限制和 scripts/stage6_demo.py 的真实进程重启命令见根 README。
