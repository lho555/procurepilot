# 受控模型分析

独立订单分析与持久工作流使用同一适配器配置，默认离线。可选的 Chat Completions 接口只负责组织已核实事实、选择制度依据与允许的建议，不决定工具调用、计算、采购数量或审批状态。

本轮实现接口与离线合同验证，尚未运行真实模型质量评测。未指定提供方、模型或费用，也没有自动读取其他产品的模型凭据。固定任务成绩仍只表示离线流程的回归结果。

## 输出约束

应用先读取订单、库存和制度，用确定性代码计算缺口与延期，再建立事实目录。模型只收到订单号、事实目录、制度摘录和允许的建议代码，不接收用户原问题、供应商联系方式、价格、草稿、审批人或环境变量。

模型返回 JSON：

```json
{
  "finding_ids": ["stock:1", "delivery"],
  "citation_numbers": [1],
  "recommendations": ["VERIFY", "HUMAN_REVIEW"]
}
```

`finding_ids` 必须覆盖所有本次事实且不重复；引用必须非空、严格为整数、存在于本次检索结果；建议只能来自当前事实允许的代码。异常必须保留人工复核，无异常必须保留继续观察，不能建议新增采购。额外字段、自由文本、伪造事实、错误引用、工具调用、拒答和截断回答均被拒绝。

页面使用校验后的编号从原始事实、制度摘录和固定建议文案生成结论。模型可选择顺序、引用和允许的建议组合，不生成开放式解释。引用检查证明编号存在，不能证明制度的语义适用性；适用性仍需人工复核。

## 配置边界

后端只读取专用的 `PP_LLM_*` 配置。配置示例见 `.env.example`；不要在聊天、Git 或浏览器配置中填写真实密钥。

| 配置 | 含义 |
| --- | --- |
| `PP_LLM_MODE=offline` | 默认离线，不读取专用模型密钥，不连接模型服务 |
| `PP_LLM_MODE=chat_completions` | 选择兼容 JSON Chat Completions 的接口 |
| `PP_LLM_ALLOW_SYNTHETIC_SEND=true` | 确认允许将本项目合成事实与制度摘录发送给已选服务；与模型模式同时设置才启用 |
| `PP_LLM_ENDPOINT` | 完整 `/chat/completions` URL，不允许用户信息、查询参数或片段；远端须 HTTPS，HTTP 仅允许本机回环 |
| `PP_LLM_MODEL` | 服务支持的具体模型名，应用不自动选择或替换 |
| `PP_LLM_API_KEY` | 专用后端凭据；HTTPS 服务必须配置，本机 HTTP 服务可以留空 |

确定提供方、模型、仅合成数据范围与费用上限后再配置，重启后端使 `.env` 变更生效。选择模型模式后，点击分析或确认创建工作流可能产生一次在线请求；这是服务端配置许可，适用于可信本机使用，不提供多用户身份或计费权限。

请求使用 `response_format=json_object`、`max_completion_tokens=1024` 和 `store=false`，不携带工具定义。服务须支持这些字段以及标准 `choices/message/content/finish_reason` 响应；兼容性须在选择服务后单独验证。接口协议参考 [OpenAI 官方 Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。`store=false` 是请求参数，不能代替提供方的数据处理约定。

每次分析最多一次模型请求，不自动重试、不跟随重定向、不继承代理或证书覆盖配置，保持 TLS 校验。模型上下文最多 24,000 UTF-8 字节，HTTP 响应最多 64,000 字节；网络不活动超时 10 秒，模型步骤总上限 12 秒，完整分析仍受 25 秒总超时约束。传输使用已有 HTTPX 依赖，参见 [HTTPX 异步接口](https://www.python-httpx.org/async/) 与[环境变量控制](https://www.python-httpx.org/environment_variables/)。

这些限制不构成账户总费用配额。共享部署前仍需身份、额度、并发和审批权限控制；当前不连接生产数据或真实采购系统。

## 失败与恢复

缺少配置或制度、服务错误、超时、无效输出会明确失败，不自动降级为离线成功。原始服务错误、响应和密钥不写入分析轨迹或 checkpoint。失败时保留确定性业务事实和安全错误说明，工作流停在 `ANALYZE/FAILED`，不创建采购草稿或执行回执。

修复配置后可人工确认恢复；也可显式切换离线模式后恢复，保存结果会如实标记离线模式。已经提交分析 checkpoint 的工作流不重复请求模型，且错误的后续模型配置不会阻塞该运行的审批与执行确认。

分析 checkpoint 提交前进程崩溃，或人工重新分析，仍可能再次调用模型并产生费用；不承诺跨进程模型计费幂等。批准与执行仍是两次独立人工确认。

## 重复验证

```powershell
./.venv/Scripts/python.exe scripts/verify_delivery.py --confirm
```

统一命令、后端测试与 Golden Tasks 的 API 子进程均显式固定离线模式，优先于 `.env`。模型接口测试使用 HTTPX 本地响应替身验证请求、校验、失败、审批和恢复，没有真实模型请求。当前证据不代表模型理解准确率或特定提供方兼容性。

另有 `scripts/run_model_evals.py --confirm` 与工作台“模型验收”，可独立回放内置或本地记录的响应，对照固定合成标注检查引用适用性和建议覆盖；同样不连接模型服务。用法、指标分母及未测量的时延 / 费用说明见 [MODEL_EVALUATION](MODEL_EVALUATION.md)。
