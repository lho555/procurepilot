# evals

Stage 7：17 条固定 Golden Tasks，真实 API / stdio MCP，隔离 SQLite，实际 API 进程重启恢复。

项目根目录运行 `./.venv/Scripts/python.exe scripts/run_evals.py --confirm`；无需外部服务或 PYTHONPATH。
每次创建 .local/evals/<唯一ID>，保留报告 / 证据 / 合成数据库，不连接既有 MySQL，不清空数据。

- golden_tasks.json：可核对预期状态、工具参数、事实、制度、确认 / 拒绝和效果差值。
- runner.py：固定场景执行、数据库效果核对、适用性和错误处理、报告写入。
- scorer.py：精确工具 / 参数、来源哈希 / 行号 / 摘录、指标分子分母、Markdown 摘要。
- server.py：固定命令启动自有 loopback API，唯一响应头确认所有权，停止 / 重启 / 清理。

指标：Task Success、Tool Selection、Tool Argument、Citation、Approval Correctness、Unauthorized Action、Recovery。
成功比例越高越好；Unauthorized Action 是违规任务比例，越低越好。不适用项排除，适用失败计入失败分母。
任务失败返回非零退出码；所有 17 条通过才报告 passed=true。
评分器测试包含错误参数 / 多余工具 / 伪造引用 / 错误分母的负例，防止把错误行为计为正确。

范围是 Mock 合成确定性工作流，不能冒充真实 LLM 的自主选工具 / 推理质量、生产安全或泛化能力。
恢复场景使用应用层 checkpoint；已保存分析不重跑，未保存的分析内部续跑仍未实现。
Stage 8 工作台已支持浏览器内导入报告；不上传、不执行评测，也不认证报告真实性。
