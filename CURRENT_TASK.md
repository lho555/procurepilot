# 当前任务

## 交付复现基础：本地验收完成

分支：`chore/reproducible-delivery`。业务 Stage 0～8 已完成，当前完善独立安装、统一验证入口与基础 CI。

- 以公开仓库为后续开发主线，原私人工作区不作为推送来源。
- 新环境按锁定文件安装，不复制旧虚拟环境、node_modules、配置或数据库。
- 新增 `scripts/verify_delivery.py --confirm`，验证依赖、后端、前端、业务闭环与 HTTP 启动链路。
- GitHub Actions 使用相同命令，声明 Windows / Ubuntu 矩阵，不使用模型密钥、真实业务数据库或生产系统。
- 本轮 119 项后端测试、17/17 Golden Tasks、前端构建和 3 项启动检查通过；详情见 HANDOFF.md 和 docs/evidence/delivery-20261018.json。GitHub Actions 尚未触发，Ubuntu / 远端 CI 未验证。

下一纵向切片：Stage 9 受控真实模型分析，详见 CODEX_TASKS.md。当前不接入模型、不读取密钥或产生模型费用。
