# 百炼 qwen3.7-flash 验收

已核对的模型配置为百炼华北 2（北京）旧域名 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`、模型 `qwen3.7-flash`。官方仍支持该域名，本切片沿用已选配置，不自动迁移业务空间地址。

## 显式关闭思考

选用 `PP_LLM_PROVIDER=bailian` 后，应用在 HTTP 请求 JSON 顶层固定发送：

```json
{
  "model": "qwen3.7-flash",
  "enable_thinking": false,
  "temperature": 0,
  "max_completion_tokens": 1024
}
```

HTTPX 直接请求不需要 `extra_body`。保留 JSON 输出与全部事实 / 引用 / 建议校验；百炼配置不发送其文档未列出的 `store` 字段，不能由此推断提供方的数据留存政策。原通用协议仍发送 `store=false`，其行为保持不变。

非秘密示例见 [bailian.env.example](../profiles/bailian.env.example)。它保持 `PP_LLM_MODE=offline` 与发送许可关闭，密钥为空。日常应用要在线运行仍需另行配置本机密钥、模型模式和合成数据发送许可；复制示例不会自动调用模型。专用百炼配置仅接受上述已核对的地址和模型。

协议依据：[百炼官方 Chat 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。`enable_thinking=false` 明确关闭思考，`max_completion_tokens` 限制完整输出；官方说明实际输出可能比设定多最多 10 个 Token，因此预算计入这部分余量。

## 一次性验收计划

本轮仅对固定合成题集的前四个正例调用模型：缺货并延期、仅缺货、仅延期、无异常。没有 embedding 请求或额外连通性探测。原有 8 个失败案例继续离线验证，不能将它们计为真实模型拒答成绩。

```powershell
./.venv/Scripts/python.exe scripts/run_bailian_acceptance.py --prepare --confirm-local --budget-cny 12
```

此命令只准备 `.local/bailian-acceptance/<ID>/plan.json`，不读取模型凭据或访问模型服务。计划包含 24 小时有效期、源码 / 题集摘要、四份请求体摘要、模型、地址、关闭思考参数与预算；超过有效期、文件变化、修改参数或预算不符时拒绝执行。

按 2026-10-19 核对的 [qwen3.7-flash 官方模型限制与北京区价格](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)，采用最高档输入每百万 Token 1.2 元、输出 4.8 元，不扣除缓存、免费额度或促销：

| 项目 | 保守预留 |
| --- | --- |
| 单次输入 | 模型最大输入 991,808 Token |
| 单次输出 | 1024 + 10 = 1034 Token |
| 单次费用 | `(991808 × 1.2 + 1034 × 4.8) / 1000000 = 1.1951328` 元 |
| 最多四次 | `4.7805312` 元 |
| 本轮费用上限 | 12 元 |

这是按公开价格计算的本地保守预留，不是预计实际用量、供应商预扣款或账单。与其他项目的预算独立，不继承它们的授权或剩余额度。价格需要在新的验收授权前重新核对。

## 执行边界

取得该轮凭据访问与在线调用许可后，可执行准备好的计划：

```powershell
./.venv/Scripts/python.exe scripts/run_bailian_acceptance.py --execute <计划文件> --confirm-live --credential-file <本机凭据.env> --credential-name <指定密钥字段名>
```

只解析用户明确指定的单个字面量赋值，不展开其他环境变量，不复制密钥到项目配置、报告或 Git。指定字段缺失、重复、间接引用或无效时停止。秘密值只存在于执行进程内存中并用于认证请求。

执行器先以独占文件创建并同步保存整轮预留，再读取凭据；每次请求前另存一份预留记录。最多四次，不重试、不降级；同一计划一旦领取，就算凭据错误、超时或进程中断也不能重跑，不自动退回保守预留。并发只能有一个执行者读取凭据或调用模型。磁盘文件若被人为删除 / 改写，记录不能作为权限或账单认证，因此禁止通过删文件重置计划来绕过本轮限制。

传输或输出格式失败会停止后续调用。契约有效但不符合固定题集标注时，如实记录质量失败，仍可完成余下预定样本，不通过改提示词或追加调用追求满分。凭据访问失败也会消耗该计划的执行资格，但不等于已经向供应商发送请求。

## 证据与保留限制

结果写入该计划目录的 `live-report.json`，含验证后的受限选择、提供方 usage 的有界整数、请求端到端耗时、按最高档单价估算的费用及逐案例标注检查。不保存原始响应、错误正文、认证头、密钥或凭据文件路径。usage 缺失或不可信时不记为免费，保留整轮预留并标记验收未完整通过；usage 费用估算不代替供应商账单。

报告类型为 `bailian_live_acceptance`，与工作台“模型验收”的离线回放报告不同，不强行导入该页面或冒充离线成绩。四个固定合成样本的标注检查不能证明人工正确率、泛化质量、真实业务制度适用性或生产权限安全。它只观察模型接口；真实采购、审批身份与生产系统不在范围内。

12 元上限仅适用于这轮一次性验收程序，不是普通 `/agent/analyze` 的账户级额度控制。默认应用与完整回归保持离线，不会自动执行此程序。
