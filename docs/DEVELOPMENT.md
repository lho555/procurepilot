# 开发与交付验证

## 开发主线

从公开仓库 `main` 创建功能分支；私人审计材料、工作聊天、本机配置和发布临时目录不进入项目。每次只开发 CODEX_TASKS.md 中一个纵向切片。

依赖安装与服务启动见 [QUICKSTART](QUICKSTART.md)。建议使用独立虚拟环境，不复用其他项目的 `.env` 或数据库。

## 统一验收

安装锁定依赖后，在项目根目录使用该环境的 Python：

```powershell
./.venv/Scripts/python.exe scripts/verify_delivery.py --confirm
```

Linux / macOS 使用 `./.venv/bin/python`。命令会依次执行：

1. Python 依赖一致性检查。
2. 后端测试，使用本次运行目录中的临时库。
3. TypeScript 检查与前端构建。
4. 离线回放 12 个模型响应案例，检查事实约束、引用标注、建议覆盖与失败处理，不读取模型凭据。
5. 新建隔离 SQLite 与制度索引，运行全部 Golden Tasks，包括实际 API 重启恢复。
6. 启动自有 API / Vite 进程，核对后端健康、前端入口与 API 代理，结束后清理这些进程。

`--confirm` 仅批准隔离合成数据和本地验证文件写入，不授权访问真实数据库、调用外部模型或执行真实采购。不提供该参数时命令拒绝运行。

结果位于 `.local/delivery/<run_id>/report.json`，包括步骤退出码、耗时、源码提交、工作区运行文件摘要及关联的业务 / 模型回放报告。详细日志和测试 XML 保存在同目录；失败返回非零，不使用旧、失败或宣称在线调用的模型回放报告代替本次结果。模型报告可在工作台“模型验收”页导入，详见 [MODEL_EVALUATION](MODEL_EVALUATION.md)。

脚本隔离继承的数据库、Python 导入路径、模型密钥、Vite 环境变量与追踪配置；不会覆盖 `.env`、停止其他服务或清空现有数据库。任何步骤失败都应先检查该步骤日志，不直接重复业务写入。

## CI

`.github/workflows/verify.yml` 在 pull request、main 推送或手动触发时使用相同入口，矩阵为 Windows / Ubuntu、Python 3.14、Node.js 24。工作流使用只读仓库权限、固定的 Action 提交，不注入模型或业务系统密钥。

CI 仅上传统一结果 JSON 与测试 XML，不上传虚拟环境、数据库、原始业务响应或 `.env`。实际远端运行是否通过，以 Actions 记录为准；本地 Windows 结果不能替代 Ubuntu 验证。

## 验证边界

全新虚拟环境安装与空缓存 npm 安装不等于在全新操作系统上验证。环境最低版本、15 分钟启动、跨平台、MySQL 与浏览器交互的覆盖范围须分别记录。不要把 HTTP 启动检查当成完整审批 UI 端到端验收。

现有固定任务仍保留为确定性回归；统一命令和评测 API 显式设置 `PP_LLM_MODE=offline`，不会因 `.env` 的模型配置意外调用模型。可选模型入口、失败和审批边界见 [受控模型分析](MODEL_ANALYSIS.md)。真实模型质量需单独评测、手动调用并明确费用。共享部署前先补身份与审批权限。
