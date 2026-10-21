# 当前任务

## Stage 9：已知 usage 结算与最新版四场景整轮通过（2026-10-21）

分支：`feature/verified-usage-settlement`，从 main 创建后快进到 PR #12 的 `d6951b9`；基于 `feature/closed-reservation-settlement`，不自动合并 main。

- 用户明确批准新结算方案及四例各一次。独立追加 usage-reservation.json：终止证明、原计划 / 领取、全部标记 / 结果和逐项 usage / 最高费率均匹配才缩减；异常 / 缺失 / 零 usage 仍全额保留。每次预检 / 领取重新验证，变化或部分写入阻断。两种结算必须先未用槽位、后 usage，旧计划不能重跑。
- 已追加 4 份 usage 结算，覆盖此前 7 次已知调用，按最高档费率保留 0.0072396 元；3 次未知调用仍保留 3.5853984 元。原有 39 份计划 / 领取 / 报告 / 标记 / 结果与既有结算文件逐字节不变。
- 在同一最新版执行 shortage_and_delay、shortage_only、delay_only、healthy_order，各一次，共 4 次请求 / 4 个有效结果，四例事实约束、引用适用性、建议覆盖全部通过。没有失败、重试或额外探测，批准次数已用完，模型调用结束。
- 本次完整预留 4.7805312 元继续保留，不递归结算扩大范围。合计有效保守预留 8.3731692 元、未预留 3.6268308 元，原现金上限仍 12 元。新四例 usage 最高费率估值 0.0041964 元，不是账单；历史 3 次用量未知，项目实际总费用仍未知。不依赖共享免费额度。
- 完整离线交付通过：390 项后端测试、12 个模型回放、17 个 Golden Tasks、前端类型 / 构建和 3 项 HTTP 启动检查。新增 40 项回归，针对性共 207 项通过。运行提交 `d756d0585f9225b7cd11fc1980e32b790dec4772`，源码摘要 `caf18aae347e3a07e04ee435224d92ad8649672d8a8eafb0a7eaee1091501447`；提示词和四个请求内容摘要保持上轮最终版本，没有新依赖或前端 / 业务审批改动。

在线证据：[latest-four-case-20261021.json](docs/evidence/latest-four-case-20261021.json)。离线证据：[verified-usage-regression-20261021.json](docs/evidence/verified-usage-regression-20261021.json)。说明：[BAILIAN_ACCEPTANCE](docs/BAILIAN_ACCEPTANCE.md#最新版四场景整轮验收2026-10-21)。

当前切片已完成；下一步审阅本 PR 与依赖链，复核四例标注和制度适用性。真实负例与独立人工复核仍待完成，不将四个固定合成正例宣称为完整质量、准确率或生产验收。Stage 10 未启动；本轮不追加模型请求、不合并 main。
