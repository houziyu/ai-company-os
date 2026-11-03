# 阶段 03 独立审查 R1–R13 修复交接

日期：2026-11-02（Asia/Shanghai）。[修复验收](../acceptance/phase-03-fixes.md)｜[原独立审查](../acceptance/phase-03-review.md)｜[原审查交接](phase-03-review.md)

## 本阶段范围

**已完成**：修复独立审查的 R1–R13（7 项 P1、6 项 P2），并建立正确行为回归；重跑后端全量、前端检查、迁移往返、真实双 Worker/强杀、nginx 后 SSE 断线补读、四视口 UI 验收。新增迁移 `e5b7c9d1a2f4`。

**未完成**：无失败项。真实模型调用、真实浏览器 200% 缩放、阶段 02 强杀恢复、R13 真实浏览器断网、完整 Compose 整链未执行（见验收第八节）。

**明确不在本轮范围**：阶段 04 的隔离执行器、五岗位交付闭环、提案审批、知识库、长期项目记忆；发版、推送、生产部署。

## 工程入口

| 路径 | 作用 |
|---|---|
| `backend/app/services/execution_service.py` | 队列领取、租约、fencing、停止门禁、恢复与结算（R1–R6、R9 的核心） |
| `backend/app/services/budget_service.py` | 三层账户、预留/结算/释放、待核对投影（R5、R10） |
| `backend/app/services/event_feed_service.py` | 提交顺序发布游标（R8，新增） |
| `backend/app/services/task_service.py` | 任务创建/取消/重试状态机、预算依赖注入（R7、R9、R11） |
| `backend/app/workers/demo_processor.py` | 演练处理器；停止门禁结果如实上报（R1、R6） |
| `backend/app/workers/task_worker.py` | Worker 循环；恢复不再逃出异常（R4） |
| `backend/app/api/v1/tasks.py` | 查询参数契约、待核对接口、SSE 游标（R8、R10、R12） |
| `frontend/src/api/task-submission.ts` | 前端幂等键生命周期（R13，新增） |
| `frontend/src/views/ProjectsView.vue` | 提交按钮使用上述键（R13） |
| `backend/tests/test_phase03_review_fixes.py` | **正确行为回归，34 项**（新增） |
| `frontend/src/__tests__/phase03-idempotency.spec.ts` | 前端键生命周期回归，7 项（新增） |
| `backend/alembic/versions/e5b7c9d1a2f4_phase_03_publication_cursor.py` | 迁移（新增） |
| `scripts/verify_phase03_fix_migration.py` | 迁移往返与数据保留验证（新增） |
| `scripts/verify_phase03_nginx_sse.py` | nginx 代理后 SSE 断线补读验证（新增） |
| `scripts/verify_phase03_runtime.py` | 真实进程验证（本轮增加 `stop-gate-after-claim`、`cancel-running-flag`） |
| `docs/acceptance/evidence/phase-03-review/test_review_repros.py` | **历史诊断，保持原样**；现在 13 项全部失败即为修复生效证明 |

### 实际可用的检查命令

```powershell
# 独立 PostgreSQL（示例端口 55440；不要指向真实数据库）
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://<user>:<pass>@127.0.0.1:<port>/postgres'
cd backend

# 缺陷是否仍存在：修复后应全部 FAILED
./.venv/Scripts/python.exe -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-review/test_review_repros.py -q

# 正确行为回归（应 34 passed）
./.venv/Scripts/python.exe -m pytest tests/test_phase03_review_fixes.py -q

# 全量与静态
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app

# 迁移往返（自建临时库并删除）
./.venv/Scripts/python.exe ../scripts/verify_phase03_fix_migration.py

# 真实进程（需先对目标库执行 alembic upgrade head）
./.venv/Scripts/python.exe ../scripts/verify_phase03_runtime.py --database-url '<isolated-db-url>'

# nginx 后 SSE（需先迁移目标库；需 Docker 与 nginx-unprivileged 镜像）
./.venv/Scripts/python.exe ../scripts/verify_phase03_nginx_sse.py --database-url '<isolated-db-url>'

# 前端
cd ../frontend; pnpm check
```

相关配置变量（仅名称）：`COMPANY_DEFAULT_BUDGET_TOKENS`、`PROBE_RESERVATION_TOKENS`、`WORKER_LEASE_SECONDS`、`WORKER_POLL_INTERVAL_SECONDS`、`DEMO_SLOW_STEP_SECONDS`。本轮未新增配置变量。

## 变更

### 数据表与迁移

- 新增迁移 `e5b7c9d1a2f4`（`down_revision=c41f7b93e2a5`）：`domain_events.published_seq BIGINT NULL`、检查约束 `published_seq >= 1`、唯一部分索引 `uq_domain_events_published_seq`、部分索引 `ix_domain_events_unpublished`；按 id 顺序回填历史行。
- 升级：加列/索引 + 回填，历史行相对顺序不变，旧 `Last-Event-ID` 可继续续读。
- 降级：删除列与索引，SSE 回退到按主键排序（可能重现 R8）；不删除业务数据。降级到 `8c24a71d9e03` 仍会删除阶段 03 全部表（原有限制）。
- 无其他表结构变化；未修改已发布迁移。

### API 契约

- `GET /api/v1/tasks`、`/api/v1/budgets`、`/api/v1/events/stream`：**同时接受** camelCase 与 snake_case 查询参数（`projectId`/`project_id`、`ledgerLimit`/`ledger_limit`）。
- `/api/v1/budgets` 的 `pendingReconciliationCount` 与账户 `needsReconciliationCount` 现在统计**所有** `needs_reconciliation=true` 的预留（含已保守结算）。
- `/api/v1/budgets/pending-reconciliation` 返回两类记录，并新增 `status`（`RESERVED`/`SETTLED`）。
- SSE 的 `id:` 与 `/tasks/{id}/events` 的 `id` 改为**发布游标**（不是主键）。对客户端语义不变：仍是单调递增、可用于 `Last-Event-ID` 续读。
- 事件 `TASK_BLOCKED`、`TASK_INTERRUPTED` 会在“额度不足的重试”“公司暂停停止点”时发出。

### 状态机与预算语义

- 取消：`QUEUED` 立即；`RUNNING` **且存在未过期租约的执行**才协作式；`INTERRUPTED`/`BLOCKED`/`WAITING_APPROVAL` 立即取消并从调用证据解析封套。
- 暂停：已领取未开始的尝试停在 `INTERRUPTED`（`interrupted_unknown_side_effects=false`），恢复公司后需人工重试。
- 恢复：重试预留失败 → 任务 `BLOCKED`（不再抛异常）；无调用证据 → 释放封套；有调用证据 → 保守结算并标记。
- 完成：尝试用量由步骤行重建；任一未知步骤 → 整次未知。
- 自动重试与手动重试的两层预留均为单一 savepoint。

### UI

- `ProjectsView` 的提交键绑定到“一次逻辑提交 + 固定请求体”；失败保留、改体换键、成功或冲突后清除。界面交互未变（无新增按钮/颜色/尺寸）。
- 预算页沿用既有结构，新增的 `status` 字段用于区分“已完成保守结算/尚未结束”。

## 关键决定

- **不改变技术路线**：仍是 PostgreSQL 队列、fencing token、三层预算；未引入 Redis/消息队列。
- **事件游标而非主键**：主键在提交前分配，不能证明提交顺序；`published_seq` 只对已提交行分配，配合 advisory lock 串行化。发布在读取侧发生（不在业务事务内），避免持业务锁等待其他事务提交造成死锁。
- **锁序固定为 company → task → execution → budget account**，恢复与步骤写入共用；新写入者必须复用 `_lock_fenced_execution`。
- **取消标记不是用量证据**：是否释放封套取决于步骤/CALL 预留证据。
- **不自动无限重试**：`BLOCKED`/`INTERRUPTED` 交人工。
- 决策细节与理由已写入代码模块 docstring；未新增 ADR（本轮未改变架构结论，属 ADR-0004 的实现修正）。如后续希望把“提交顺序游标”升格为正式 ADR 条目，可在阶段 04 前补一份简短补充记录。

## 验收

见[修复验收](../acceptance/phase-03-fixes.md)。摘要：

- 原诊断：修复前 13 passed（缺陷存在）→ 修复后 **13 failed**（缺陷不存在）。
- 新增回归：**34 passed**；后端全量 **229 passed**；前端 **110 passed**；静态检查全通过。
- 迁移往返 PASS；真实进程 10/10；nginx SSE 3/3；UI 4 视口 0 异常、40 张截图。
- 未执行：真实模型调用、200% 缩放、阶段 02 强杀、R13 真实浏览器断网、完整 Compose 整链、生产库迁移演练。

### 对历史证据的影响

重跑 UI 脚本时其默认输出目录曾指向 `docs/acceptance/evidence/phase-03/ui`，该目录（未被版本控制）中的历史截图与 `ui-results.json` 被本轮修复后运行的产物覆盖，**无法恢复**。脚本现已支持 `PHASE03_UI_OUT`，本轮证据写入 `evidence/phase-03-fixes/ui/`。原 `phase-03.md` 的结论文字未被改写，但引用 `evidence/phase-03/ui/` 的截图现为 2026-11-02 15:49 的修复后版本。原独立审查报告与诊断脚本未受影响。

## 下一阶段接入点

阶段 04 **可以**基于本内核建设隔离执行器，但必须遵守：

1. 任何新的执行写入复用 `ExecutionService` 的 fencing/停止门禁，不自行 `SELECT` 后写。
2. 真实供应商调用在 `start_step` 之后发起；用量未知一律 `usage_known=false`，不得写 0。
3. 取消是协作式：只在步骤边界停止；不得声称已中止进行中的外部调用。
4. 事件读取按 `published_seq`（`EventFeedService.read_after`）；不要在业务事务内发布会等待其他提交的锁。
5. `BLOCKED`/`INTERRUPTED`/待核对项需要有运营处理入口（阶段 04+），当前只有只读页面。
6. 新迁移只追加；`e5b7c9d1a2f4` 已成为 head，不要修改其内容。

已知问题（不阻塞阶段 04）：保守结算可能高估；待核对无自动核对流程；暂停后需人工重试；事件发布最多延迟一个轮询间隔。
