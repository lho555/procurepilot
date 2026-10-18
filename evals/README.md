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

## Stage 9 模型响应回放

`scripts/run_model_evals.py --confirm` 使用 `model_cases.json` 与摘要绑定的 `model_responses.json`，复用实际模型适配器，只使用 HTTPX 本地响应替身。12 个合成案例覆盖事实 / 引用 / 建议及失败边界；`--responses <文件>` 可回放同题集的完整本地响应记录。

`model_replay.py` 按固定标注计分，适用失败保留在分母；报告不包含原始响应或错误文本。没有真实模型调用，不读取模型凭据；本地回放时延不能解释为模型时延，模型费用保留未测量。报告在独立的“模型验收”页导入，Stage 7 报告入口保持不变。详见 [模型验收说明](../docs/MODEL_EVALUATION.md)。
