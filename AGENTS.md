# ProcurePilot 开发约定

目标是可运行、可解释、可恢复的采购与供应链协同应用。先阅读 README.md、CURRENT_TASK.md、CODEX_TASKS.md 和 HANDOFF.md，再修改当前纵向切片。

- 一次实现一个业务切片，沿用模块化单体和已锁定依赖。
- 默认使用合成数据与本地服务；所有业务写入保留显式确认。
- Agent 默认只读，不自行增加权限；确定性计算不交给模型。
- 不连接生产系统，不把凭据、数据库或 `.local` 原始结果提交到仓库。
- 公开提交使用维护者确认的 noreply 邮箱；不导入私人工作区历史。
- 从公开 `main` 创建功能分支。提交或推送前检查差异，发布与外部系统操作遵循用户授权。
- 安装与运行以 docs/QUICKSTART.md 为准；开发与阶段验收以 docs/DEVELOPMENT.md 为准。
- 使用已安装依赖的隔离验收：`python scripts/verify_delivery.py --confirm`。命令会运行后端测试、前端构建、Golden Tasks 和启动检查，并保存 `.local/delivery/<run_id>/report.json`。
- 开发过程中先执行受影响的最小检查，阶段结束再运行完整验收；不为文档修改重复业务全套测试。
- 更新 CURRENT_TASK.md 和 HANDOFF.md，区分本轮运行与历史证据，不用固定场景成绩宣称真实模型准确率或生产能力。
