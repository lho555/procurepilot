# 开发交接

## 跨平台 CI 已通过，PR 待审阅（2026-10-18）

- 已获用户明确授权，推送 `chore/reproducible-delivery` 并创建 [PR #1](https://github.com/lho555/procurepilot/pull/1)，未合并 main。
- 代码提交 `d4bfd94` 的 [CI 第 1 轮](https://github.com/lho555/procurepilot/actions/runs/39088666148) 已完成：Windows / Ubuntu 均为 success，安装、统一验收和结果上传通过，无失败步骤。
- 远端工作流摘要与 artifact ID / 摘要见 [ci-delivery-20261018.json](docs/evidence/ci-delivery-20261018.json)。构件保留 7 天；不包含数据库、原始业务响应或凭据。
- 本次结果记录更新仅涉及文档、证据和 SHA256SUMS，运行源码保持与已通过的本地验收一致。更新提交会自动再触发 PR CI，最终状态以 PR 检查为准，不将首轮结果冒充其他提交的运行。
- 当前交付复现切片已完成跨平台验证；下一步先审阅本 PR，再安排 Stage 9。最低版本、macOS、15 分钟启动及本轮 MySQL 仍未验证。下方保留此前本地验收时点的记录。

## 交付复现基础：本地验收完成（2026-10-18）

分支：`chore/reproducible-delivery`，基于公开 `main` 的 `414085d`，没有导入私人工作区历史。Stage 0～8 业务能力保持不变；Stage 9 尚未启动。

本轮新增：独立开发入口、隔离验收 CLI、基础 GitHub Actions，以及当前任务和阶段接续文档。公开验收摘要见 [delivery-20261018.json](docs/evidence/delivery-20261018.json)。

### 实际验证

- Windows / Python 3.14.3 / Node.js v24.14.0。新建虚拟环境，pip 禁用缓存安装原锁定依赖；空 node_modules 与新 npm 缓存安装通过，没有复制旧依赖、配置或数据库。
- `./.venv/Scripts/python.exe scripts/verify_delivery.py --confirm` 通过；本地完整结果为 `.local/delivery/eeeecd2ed12a48c8baa44f70c41daf8b/report.json`。依赖检查、119 项后端测试、前端类型检查与构建、17/17 Golden Tasks、3 项启动 HTTP 检查全部通过。
- 业务场景使用隔离合成 SQLite，包含实际 API 重启、审批 / 拒绝、独立执行确认和幂等边界。API / Vite 进程已清理，复查自有端口均无监听。
- 新增 3 项针对性测试覆盖环境隔离、旧 / 外部 / 失败报告拒绝，以及清理父子进程且保留其他进程。首次直接 pytest 因默认系统临时目录拒绝访问产生 2 项 setup error；使用新工作区独立 `--basetemp` 后通过，未修改系统权限或原有测试。
- 无 `--confirm` 拒绝运行且不写文件；工作流 YAML 解析、只读权限、4 个官方 Action 固定提交和本地文档链接检查通过。
- 验收时 HEAD 为 `414085d`，未提交运行文件绑定源码摘要 `79f093a6969edab3c66c95a547fc57ae3bf2500da0191b7f9d946712a527e13b`；算法和逐文件摘要见公开摘要的 source 字段。不把旧 commit 伪称为已包含本次修改。

### 保留限制

GitHub Actions 尚未触发，Ubuntu 仅有矩阵配置，没有实际通过结果；工作流应在推送 / PR 后检查。未验证全新操作系统、最低版本、15 分钟启动或本轮 MySQL；HTTP 检查不代表完整浏览器审批验收。前端大 chunk 警告保留。

### 下一步

本地分支可供审阅；按用户授权安排远端 CI 后，再进入 CODEX_TASKS.md 的 Stage 9。真实模型接入需确认提供方、模型、数据和费用，不自动读取已有密钥或调用付费服务。已有历史业务验证继续保留在 docs/VERIFICATION.md，和本轮结果分开解释。
