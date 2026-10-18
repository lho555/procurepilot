# 开发交接

## Stage 9 第一切片：受控接口与离线验收（2026-10-18）

- 当前分支 `feature/controlled-model-analysis`；从公开 main 创建后，快进包含交付复现基础的 `20cfbeb`，本切片依赖 PR #1。main 没有合并，原私人历史没有导入公开开发仓库。
- 新增 `backend/app/model_explanation.py`：专用双开关、受限服务地址、最小事实目录、严格 JSON 选择与确定性渲染。独立分析和持久工作流通过同一工厂创建适配器；失败配置只影响新的分析，不阻塞已有 checkpoint 的审批 / 恢复。
- 模型最多一次请求，不自动重试或降级，隐藏原始服务错误；保留确定性事实与安全失败原因。缺少制度、错误引用、虚构 / 遗漏事实、越权代码、工具调用、拒答、截断、超限和超时均不会创建草稿。模型输入不含用户原问题、联系方式、草稿或审批人。详细边界见 [MODEL_ANALYSIS](docs/MODEL_ANALYSIS.md)。
- 页面标明运行模式，并展示持久分析失败原因；去除首页硬编码“离线 Mock”，保持合成数据和本地执行事实边界。统一 CLI、评测 API 子进程和 pytest 显式固定离线模式，不因 `.env` 配置触发真实模型。

### 本轮验证

- 首轮针对性命令运行 `backend/tests/test_model_explanation.py` 与 `backend/tests/test_agent.py`，56 项通过；随后新增的取消清理用例随完整验收通过。
- `./.venv/Scripts/python.exe scripts/verify_delivery.py --confirm` 全部通过：158 项后端测试、前端类型检查与构建、17/17 Golden Tasks、3 项 HTTP 启动检查。未修改锁定依赖、业务审批接口、数据库结构或生产配置。
- 原始报告 `.local/delivery/4a7ecf44bcdf443f8c757816674d8526/report.json`；可公开摘要 [model-analysis-20261018.json](docs/evidence/model-analysis-20261018.json)。验收时 HEAD 为 `20cfbeb`，本次工作区运行源码摘要为 `e6e1bf5328431d5cb7626beea6cc0d3ba1d2646551cbe1b98d06a3ad44f914b0`，不是把旧提交冒充本次代码。
- Playwright 本机缓存 Chromium 的 4 项检查通过：真实离线 API 分析；用浏览器本地响应替身验证模型成功模式、模型失败状态和持久失败原因 / 执行禁用。报告 `.local/model-ui/report.json`，脚本 `.local/model-ui/check.js`，截图仅在本地。默认 Chrome 不存在，改用已安装 Chromium，没有安装或下载浏览器。没有页面脚本异常；favicon 404 为既有资源请求，不影响业务验收。
- 所有本轮启动的 API / Vite / 浏览器均已关闭。新增模型测试使用 HTTPX 响应替身，未访问真实模型、现有密钥或付费服务。改动文本的个人路径 / 邮箱 / 常见凭据规则与文档链接检查通过，公开提交继续使用 noreply 邮箱。

### 待办与限制

真实提供方 / 模型 / 数据范围 / 费用尚待确认，本切片不代表真实模型理解质量或任意兼容服务已验证。引用只核对存在性，不能证明制度适用性；受限 JSON 选择不能代替开放式推理。分析 checkpoint 提交前崩溃、人工重跑或失败后恢复可能再次计费，不承诺模型计费幂等或账户总预算控制。

沿用已获推送和写入授权提交功能分支及 PR，远端结果以该 PR 对应提交为准；不合并 main、不触发在线模型调用。模型质量验证完成前不启动下一业务切片。前端体积警告、最低版本、macOS、本轮 MySQL、身份与生产权限等原限制继续保留。下方为上一切片历史。

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
