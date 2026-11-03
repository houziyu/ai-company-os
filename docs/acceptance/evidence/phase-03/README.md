# 阶段 03 证据索引

日期：2026-11-02。报告：[阶段 03 验收](../../phase-03.md)。交接：[阶段 03 交接](../../../handoff/phase-03.md)。所有文件均为本轮实际执行产物；中间失败日志一并保留，不作删除。

## 最终结论文件（这些才是「通过」）

| 文件 | 内容 |
|---|---|
| `backend-full.txt` | `pytest -q` 全量结果（195 passed） |
| `migration-process.txt` + `migration/migration-results.json` | 迁移 5 项：阶段 02 基线、升级 head、`PAUSED` 持久化、降级、再升级保留历史 |
| `runtime-process.txt` + `runtime/runtime-results.json` | 真实 uvicorn + **两个真实 worker 进程**的 8 项检查，含操作系统级强杀 |
| `runtime/process-logs/{api,worker-1,worker-2}.log` | 上述进程的结构化日志 |
| `compose-process.txt` + `compose/compose-results.json` | 两个 worker **容器**的 4 项检查，含 `docker kill` |
| `compose/compose-ps.txt`、`compose/worker-logs.txt` | 容器状态与两容器日志 |
| `restart-process.txt` + `restart/restart-results.json` | 整栈停机再启动的 4 项检查 |
| `restart/compose-ps-after-restart.txt` | 重启后的容器状态 |
| `ui-run.txt` + `ui/ui-results.json` | 四视口 UI 验收总结果（0 页面异常） |
| `ui/*.png` | 40 张截图，见下表 |
| `ui-api.log` / `ui-worker.log` | UI 验收期间真实 API 与 worker 的日志 |

## 截图清单（每个视口 10 张）

视口：1440×900、1024×768、390×844、360×800。

| 基名 | 内容 |
|---|---|
| `board-<vp>.png` | 任务看板（只渲染有任务的状态列，空状态在工具条说明） |
| `board-list-<vp>.png` | 列表视图（手机默认视图） |
| `create-task-drawer-<vp>.png` | 键盘填写中的新建任务抽屉 |
| `task-succeeded-<vp>.png` | 任务详情：成功 + 演练标签 |
| `task-failed-<vp>.png` | 任务详情：失败 + 重试入口 |
| `task-unknown-usage-<vp>.png` | 任务详情：用量未知，显示「待核对」与「不会记为 0」 |
| `task-sse-disconnected-<vp>.png` | 任务详情：事件流中断提示 |
| `execution-detail-<vp>.png` | 执行详情：步骤时间线 + 本次执行账本 |
| `budgets-<vp>.png` | 预算：公司/项目四值、待核对、账本明细 |
| `overview-<vp>.png` | 概览：真实指标、近期任务、阻塞提示 |

## 中间过程文件（不代表最终结论）

| 文件 | 为什么保留 |
|---|---|
| `ui/ui-failure.txt`（如存在） | 某次 UI 运行的首个断言失败；每次成功运行会重写 `ui/`，最终目录不含失败文件即表示最后一次通过 |
| 各 `*-process.txt` 中的历史输出 | 脚本每次运行覆盖自身输出；最终内容对应本文档日期 |

## 复现

```powershell
# 1) 迁移证据（自建并删除专用库 acos_phase03_migration）
cd backend
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe ../scripts/verify_phase03_migration.py

# 2) 真实进程证据（需要隔离库，脚本会建/用 acos_phase03_runtime）
$env:PHASE03_DATABASE_URL='postgresql+psycopg://<user>:<pass>@127.0.0.1:<port>/acos_phase03_runtime'
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe ../scripts/verify_phase03_runtime.py

# 3) 容器证据（隔离 Compose 项目 acos-phase03-test，端口 18085）
cd ..
./scripts/phase03_test_stack.ps1 -ComposeArgs @('up','-d','--build','--scale','worker=1','--scale','worker2=1')
./backend/.venv/Scripts/python.exe scripts/verify_phase03_compose.py
./backend/.venv/Scripts/python.exe scripts/verify_phase03_restart.py
./scripts/phase03_test_stack.ps1 -ComposeArgs @('stop')   # 保留卷与容器

# 4) UI 证据（真实构建 SPA + 真实 API + 真实 worker）
cd frontend; pnpm build; cd ..
# 在隔离库上启动 API（8010）与 worker（任意 WORKER_NAME），然后：
$env:NODE_PATH='<含 Playwright 的 node_modules>'
$env:PHASE03_UI_BASE_URL='http://127.0.0.1:8010'
node scripts/verify_phase03_ui.cjs
```

## 数据边界

- 所有后端测试与脚本只使用 55433 上的随机克隆库或专用库（`acos_phase03_runtime`、`acos_phase03_migration`、`acos_phase03_ui`）。
- 容器证据使用 Compose 项目 `acos-phase03-test` 的独立卷 `acos-phase03-test_postgres_data`，与开发栈 `ai-company-os_postgres_data` 无关。
- 未读取或写入 `.env`；未删除任何数据卷；未推送、发布或生产部署。
