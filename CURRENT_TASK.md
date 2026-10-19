# 当前任务

## Stage 9：百炼适配与一次性真实验收准备

分支：`feature/bailian-acceptance`，从公开 main 创建后快进到 `69443f0`；依赖模型回放 PR #3，不自动合并 main。

- 新增 `PP_LLM_PROVIDER=bailian`，锁定已核对的北京区地址与 `qwen3.7-flash`，HTTP JSON 顶层强制 `enable_thinking=false`，默认仍为离线、无密钥、无发送许可。
- 新增一次性验收 CLI：先准备 24 小时有效的源码 / 请求摘要计划，再独占领取整轮预算后读取指定凭据。最多 4 个固定合成正例，不探测、不重试、不删除领取记录以重跑。
- 本轮费用上限已确认 12 元；按核对价格保守预留 4.7805312 元。凭据读取与在线执行许可尚待单独确认，目前没有实际模型请求。
- 完整离线验收通过：206 项后端测试（含 27 项百炼协议与预算边界检查）、12 个模型回放案例、17 个 Golden Tasks、前端构建和 3 项 HTTP 启动检查。公开摘要见 [bailian-acceptance-20261019.json](docs/evidence/bailian-acceptance-20261019.json)。

下一步：取得本轮凭据访问许可后，仅执行已准备的单次计划，保留真实 usage、受限选择与标注检查；结果不代替供应商账单或人工业务正确率。详细命令、预算及失败边界见 [BAILIAN_ACCEPTANCE](docs/BAILIAN_ACCEPTANCE.md)。Stage 9 的真实模型验证未完成，Stage 10 尚未启动。
