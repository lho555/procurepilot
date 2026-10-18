# 本地运行

从项目根目录执行。推荐按本次验证环境 Python 3.14.3 / Node.js 24.14.0 / npm 11.21.0 复现；声明的最低环境是 Python 3.12+、Node.js 22.12+，未逐版本验收。以下用 Windows PowerShell；首次下载锁定依赖需要网络，无真实模型或 API Key。

## 安装

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
npm --prefix frontend ci
```

本次发布复核复用了已有锁定依赖，未在无缓存机器上重新下载安装，也未验证“15 分钟启动”。

## 一条命令验收闭环

```powershell
./.venv/Scripts/python.exe scripts/run_evals.py --confirm
```

每次新建 `.local/evals/<run_id>/`，含合成库、索引、report.json / report.md 和逐任务证据。`--confirm` 仅授权隔离合成演示写入。它验证读取、引用、确认缺失、拒绝、批准后独立确认、真实 API 重启及重复请求边界；返回非零表示未通过。不会连接既有 MySQL 或清空业务库。

## 启动工作台（SQLite，无需 Docker）

若 `.env` 不存在，可复制 `.env.example`；已有配置不要覆盖。下面用当前终端变量固定本地合成库与索引，避免继承其他数据库设置：

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
$env:DATABASE_URL = 'sqlite:///./.local/procurepilot.db'
$env:RAG_INDEX_PATH = '.local/rag/index.json'
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
./.venv/Scripts/python.exe -m app.seed --confirm
./.venv/Scripts/python.exe -m app.import_knowledge --confirm
./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开终端，从项目根目录执行：

```powershell
$env:BACKEND_URL = 'http://127.0.0.1:8000'
npm --prefix frontend run dev
```

打开 http://127.0.0.1:5173，API 文档在 http://127.0.0.1:8000/docs。页面 Evaluation 可选择评测器输出的 report.json，也可选择 [发布样例报告](evidence/report.json)。UI 只展示报告，不重新验收或认证真实性。

迁移、seed 和制度导入只用于获准的本地合成库。API 启动不自动建表或 seed；`/health` 仅检查存活，不能证明数据库与索引就绪。seed 重复执行补齐缺失数据，不覆盖现有记录。制度文件变化后需再次显式导入。结束时在两个终端按 Ctrl+C。

## 其他验证入口

```powershell
./.venv/Scripts/python.exe -m pytest backend/tests -q --import-mode=importlib -o pythonpath=backend
npm --prefix frontend run build
# 独立合成库，实际停止并重启自有 API；需空闲 8016 端口
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe scripts/stage6_demo.py --confirm --database sqlite
# 已启动前后端后，只读 HTTP / MCP 分析
./.venv/Scripts/python.exe scripts/smoke_demo.py
./.venv/Scripts/python.exe scripts/stage3_demo.py --api http://127.0.0.1:5173/api
# 已有 Playwright 与 Edge 时：报告展示的只读浏览器验收
node scripts/stage8_demo.cjs .local/evals/<run_id>/report.json
```

Playwright 不是项目锁定依赖。最后一条需本机已有 `playwright` 模块，或将 `PLAYWRIGHT_MODULE` 指向现有模块；不自动下载安装。脚本输出 `.local/stage8/result.json` 和截图，拦截业务写请求；它不覆盖人工审批 UI 的完整写入流程。

## 可选 MySQL

当前版本 MySQL 路径有历史验证，本次发布复核仅使用 SQLite，未重新验证 MySQL / Docker。
本地 Docker Desktop / Compose 就绪时，使用 `.env.example` 中虚构的 `MYSQL_*` 开发值：

```powershell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
docker compose up -d --wait mysql
./.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m app.seed --confirm
./.venv/Scripts/python.exe -m app.import_knowledge --confirm
```

如果 `.env` 内仍设置了 `DATABASE_URL`，也需移除此覆盖值才能切换到 MySQL。默认仅绑定 127.0.0.1:3307，使用命名卷；`docker compose stop mysql` 保留数据。不要对已有库直接降级或删除卷；迁移前停止 API 并备份，失败后先检查实际 schema，不能假设 MySQL DDL 完整事务回滚。

## Linux / macOS

将 Python 路径改为 `./.venv/bin/python`；建环境用 `python3 -m venv .venv`。用 `mkdir -p .local` 和 `export PYTHONPATH=backend`、`export DATABASE_URL=sqlite:///./.local/procurepilot.db`、`export RAG_INDEX_PATH=.local/rag/index.json`。其余命令一致；这些平台本次未运行验收。
