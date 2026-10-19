# 开发交接

## Stage 9：安全失败分类离线补充（2026-10-19）

- 分支 `feature/safe-model-diagnostics`，从公开 main 创建后快进到 `1407153`，依赖 PR #4；沿用功能分支推送 / PR 写入授权，不自动合并 main。
- 根因是适配器丢失异常类别、验收器又统一捕获。新增 `ModelFailureKind` 固定枚举，保持原有安全消息与异常基类兼容；报告 / CLI 增加 `failure`，只含类别、固定阶段、合成案例编号和有界 HTTP 状态码。
- 记录凭据、配置、输入、超时、传输、服务状态、响应超限、输出契约与本地处理；凭据阶段超时、评分或写盘失败按阶段保守归类，未知异常不猜测为供应商失败。不序列化异常文字、类名、正文、认证头、主机名、请求 ID 或凭据路径。
- 异常仍停止后续请求、不重试、不退款。正常完成或非异常的质量 / usage 缺口为 `failure=null`，不能据此推断通过；旧报告无该字段仍表示分类未记录。预检或最终报告不能落盘时仍只返回固定 CLI 错误，领取记录保持有效。

### 本轮实际验证

- 22 项新增回归覆盖连接 / 超时、401 / 429 / 503 / 302、超限 / 无效 / 截断输出、事实约束、凭据 / 配置 / 请求准备 / 预留 / 结果验证 / 结果持久化阶段、异常字段白名单、CLI 脱敏，以及首例成功后第二例失败时保留首例并停止其余请求。
- `python -m pytest backend/tests/test_bailian_acceptance.py backend/tests/test_model_explanation.py backend/tests/test_model_replay.py -q -o pythonpath=backend --basetemp=.local/safe-diagnostics-targeted`：109 项通过；所有外部请求使用显式 HTTP 响应替身。
- 完整 `python scripts/verify_delivery.py --confirm` 通过：228 项后端测试、12 个模型回放、17 个 Golden Tasks、前端类型检查 / 构建与 3 项 HTTP 启动检查；原始报告 `.local/delivery/ca476707836a4af4b86cafb32cc62390/report.json`，运行源码摘要 `d158f51e343f37e779fbfa678ac4243486eb2df4470e9de77b87fc10c80ba401`。公开脱敏摘要见 [safe-model-diagnostics-20261019.json](docs/evidence/safe-model-diagnostics-20261019.json)。前端未改，不重复浏览器验收；未增加依赖或迁移。
- 本轮未读取现有模型凭据、未创建真实调用计划、未调用真实模型。原 claimed.json、live-report.json 和公开真实证据逐字节未变，实际尝试仍为两次；本切片不能补推上次第二次失败的根因或计费。

### 接续

完整离线验收已通过，跨平台 CI 以本提交 PR 检查为准；审阅本切片后再复核新的在线执行范围和累计预算。上轮真实计划永久保留，不重跑、不删记录重置，不将离线分类测试宣称为真实模型验收。Stage 9 整体真实验收未通过，Stage 10 未启动。字段说明见 [BAILIAN_ACCEPTANCE](docs/BAILIAN_ACCEPTANCE.md#安全失败分类离线验证)，下方保留历史。

## Stage 9 真实验收：一次执行后停止（2026-10-19）

- 用户已明确授权本轮指定凭据内存读取与在线执行。原单次计划通过有效期 / 源码 / 请求摘要 / 未领取检查后，执行一次，领取与整轮预留先于凭据读取。源配置未修改，密钥未打印、复制到项目或写入 Git。
- 执行源码提交 `0ecb97cd02546c56ba4192b0ae4bc978ac549cb9`，运行源码摘要仍为 `d1f96f053b8e1afa641ea2e5848cb31bff7096540c88f57f2b61793fcaf930fe`。该源码的 206 项后端测试、12 个模型回放、17 个 Golden Tasks、前端构建和 3 项启动检查已通过，Windows / Ubuntu [CI #5](https://github.com/lho555/procurepilot/actions/runs/39185294487) 也通过；本次更新仅为文档与脱敏证据。
- 最多四个固定正例，实际尝试两次：`shortage_and_delay` 通过契约与题集标注；`shortage_only` 未形成可验证结果后停止；`delay_only` 和 `healthy_order` 未发送。所有请求显式关闭思考，没有重试、探测、重置领取或新建替代计划。
- 首例端到端 2085.068 ms，输入 538 / 输出 73 Token，按核对最高档单价估算 0.000996 元。第二次 usage / 费用未知，因此整轮估算保持 null；12 元上限下全部 4.7805312 元预留保留，不据此退款或断言免费。
- 原始结果 `.local/bailian-acceptance/7ccae83893e34f6f98c880936fb175d8/live-report.json` 为 `passed=false / stopped`，公开脱敏证据见 [bailian-live-20261019.json](docs/evidence/bailian-live-20261019.json)。原目录的 claimed / attempt / result 文件永久保留，本计划不能再次执行。
- 当前执行器统一捕获异常而未记录安全分类，无法从保留证据判断第二次是传输、服务、输出契约还是本地处理失败；不猜测根因、不宣称整体质量通过。下一步先离线补充不泄露原始错误的分类，再复核新一轮在线范围与预算。Stage 9 整体真实验收未完成，Stage 10 未启动。

[PR #4](https://github.com/lho555/procurepilot/pull/4) 继续基于 PR #3，main 不自动合并。下方“待授权 / 尚未实际调用”为授权前历史，不据此重复申请相同凭据权限或重跑原计划。

## Stage 9 第三切片：百炼适配与一次性验收准备（2026-10-19）

- 分支 `feature/bailian-acceptance`，从公开 main 创建后快进包含 PR #3 的 `69443f0`；不导入私人历史、不自动合并 main。
- 沿用用户指定的百炼北京区兼容地址与 `qwen3.7-flash`，显式发送顶层 `enable_thinking=false`、温度 0 和完整输出上限。HTTPX 不使用 `extra_body`；百炼省略未文档化的 `store`，通用配置保持原行为。非秘密配置模板默认离线。
- `scripts/run_bailian_acceptance.py` 分离离线准备与显式在线执行：固定四个正例、24 小时计划、源码 / 请求摘要、独占领取与落盘同步均先于凭据读取；最多四次，失败停止、不重试、不退款或重置领取记录。CLI 只解析指定字面量凭据，不打印或复制秘密，不保存原始响应、错误或认证头。
- 用户已确认本轮独立费用上限 12 元。2026-10-19 核对官方价格，按模型输入最大值和输出 1024 + 10 Token、最高档单价计算整轮预留 4.7805312 元；它不是供应商预扣或预计实际费用。当前未认证调用，凭据访问与在线执行许可待确认。

### 本轮实际离线验收

- 完整 `python scripts/verify_delivery.py --confirm` 通过：206 项后端测试（新增 27 项覆盖协议字段、usage 边界、并发独占、先预留后读密钥、预算 / 过期 / 摘要变化拒绝、失败不重试及安全输出）、12 个回放案例、17 个 Golden Tasks、前端类型检查 / 构建与 3 项 HTTP 启动检查。
- 最终报告 `.local/delivery/56eb984500e14b84bb139a90015d4486/report.json`；验收时 HEAD 为 `69443f0`，运行源码摘要 `d1f96f053b8e1afa641ea2e5848cb31bff7096540c88f57f2b61793fcaf930fe`。公开脱敏摘要见 [bailian-acceptance-20261019.json](docs/evidence/bailian-acceptance-20261019.json)。
- 针对性首轮 83 项通过，补充边界后新增模块 27 项通过；最终完整验证包含全部代码。前端未改，不重复浏览器场景。未增加依赖、迁移或生产连接；完整验证保持离线。

### 接续与边界

本地已准备单次计划，具体路径在私人交接中记录。待该轮凭据访问许可后只执行一次；若过期或源码变化，不自行创建替代计划规避审核。四个真实正例的标注检查与另外八个离线失败案例应分开陈述，不以回放成绩宣称真实模型正确率。真实结果另存 `bailian_live_acceptance` 报告，不强行导入仅接受离线回放的页面。模型 usage 估算不代替账单，应用其他入口也不受本次 CLI 预算的账户级保护。

详细说明见 [BAILIAN_ACCEPTANCE](docs/BAILIAN_ACCEPTANCE.md)。沿用功能分支推送与 PR 写入授权；远端 Windows / Ubuntu 状态以本提交检查为准。Stage 9 未完成真实模型验收，Stage 10 未启动。下方为历史切片。

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
