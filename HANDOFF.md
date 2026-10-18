# 开发交接

## Stage 9 第二切片：响应回放评分与报告展示（2026-10-18）

- 分支 `feature/model-evaluation`：从公开 main 创建后快进到 `d7bdc64`，本切片依赖 PR #2；main 不自动合并，私人历史不导入公开仓库。
- 新增 `evals/model_replay.py`、12 个固定合成案例及摘要绑定的响应文件，复用实际适配器与本地 HTTP 响应替身。支持 `scripts/run_model_evals.py --confirm`，或显式提供 `--responses <本地文件>`；只写新的 `.local/model-evals/<ID>/`，输入错误在写入前拒绝，失败返回非零。
- 事实约束、引用符合题集标注、建议覆盖、失败处理与意外采纳分别计分。不适用项为 null，适用失败保留在分母。报告仅输出受限编号、建议代码和检查，不保留原始响应或原始错误，不读取模型凭据、不访问模型服务。
- 新增“模型验收”页，浏览器内读取报告并重算聚合一致性；拒绝不一致分数与在线运行声明。没有预填成绩，模型时延与费用均为未测量。详细边界和命令见 [MODEL_EVALUATION](docs/MODEL_EVALUATION.md)。

### 本轮实际验收

- 21 项新增针对性测试通过：正确回放、存在却不适用的引用、遗漏建议、失败分母、意外采纳、错题集摘要 / 重复 / 缺失记录、超限输入、报告脱敏、CLI 确认 / 退出码及统一入口拒绝旧 / 失败 / 在线声明报告。
- 最终 `./.venv/Scripts/python.exe scripts/verify_delivery.py --confirm` 通过：179 项后端测试，12/12 模型回放案例，17/17 Golden Tasks，前端类型检查与构建，以及 3 项 HTTP 启动检查。没有依赖升级、数据库迁移或业务审批接口变更。
- 最终报告 `.local/delivery/8d27596ae8a94d1da64cb42f6e0896bd/report.json`；验收时 HEAD 为 `d7bdc64`，本次运行源码摘要 `fd6f83ee2ddd276a8402678456cf460649e7bd2880bfd0cd4a6dd23ac3f4bf4f`。可公开且可导入页面的示例报告：[model-evaluation-20261018.json](docs/evidence/model-evaluation-20261018.json)，附脱敏交付验证摘要。
- Playwright 使用已缓存 Chromium，6 项浏览器检查通过：空态无预设成绩、正确报告与未测量标签、实际评分器产出的错误引用失败报告、拒绝伪造分数、拒绝在线声明，以及清除 / 无上传 / 无页面脚本异常。报告和脚本位于 `.local/model-ui/replay-checks/report.json` 与 `.local/model-ui/check-replay.js`，截图仅保留本地；既有 favicon 404 不影响功能。
- `.gitattributes` 只对两份摘要绑定的 JSON 固定 LF；模拟 `core.autocrlf=true` 的新索引检出后，两文件仍逐字节与当前文件一致，结果 `.local/model-ui/replay-checks/eol-check.json`。首轮所有业务检查虽通过，但运行期间补充该属性导致源码冻结检查按设计拒绝整轮；首轮报告 `d07d96bb38c041648f619dd5914efef0` 不作为最终通过证据，之后对最终文件集完整重验通过。
- 所有自有 API / Vite / 浏览器均已关闭。隐私规则和文档链接检查通过；未读取模型密钥、调用真实模型或产生模型费用。公开提交使用 noreply 邮箱。

### 后续接续

沿用已授权的功能分支推送与 PR 写入，远端 Windows / Ubuntu 结果以本切片对应提交检查为准；不能沿用 PR #2 的旧结果。先按依赖顺序审阅 PR，main 不自动合并。

Stage 9 的真实模型验收仍需确认提供方、具体模型、仅合成数据范围与费用上限。回放工具不认证记录来源、不观察原始模型调用时延或费用，也不证明固定标注具有真实业务权威性；尚无真实模型质量证据，Stage 10 未启动。前端体积警告、最低版本、macOS、本轮 MySQL 和生产身份边界继续保留。下方为前一切片历史。

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
