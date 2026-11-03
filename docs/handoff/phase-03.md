# 阶段 03 交接

日期：2026-11-02。验收：[phase-03.md](../acceptance/phase-03.md)。决策：[ADR-0004](../decisions/0004-phase-03-task-scheduling-and-budget.md)。证据索引：[evidence/phase-03/README.md](../acceptance/evidence/phase-03/README.md)。

## 本阶段范围

**已完成**：项目与任务、独立执行记录、执行步骤、持久事件与结构化消息、公司/项目/任务三层 Token 预算账户、预留与用量账本；PostgreSQL 持久队列、双 Worker 领取、租约/心跳/fencing token、进程退出后的中断与恢复；任务创建与预算预留、事件写入同事务；幂等键 + 内容摘要；公司暂停；协作式取消；有限自动重试（新执行 ID、历史保留）；阶段 02 探测纳入公司预算；SSE 事件 ID 与断线补读；任务看板、任务详情、执行详情、预算页与概览真实指标。

**明确不在本阶段**：隔离执行器与工具网关（阶段 04）、五岗位交付闭环（阶段 05）、提案审批（阶段 06）、真实供应商调用、真实运行模式（`REAL` 在服务端被拒，不降级）、在线改额度或「充值」（无此命令，界面不提供）。

**未完成/未验证**：真实浏览器 200% 缩放（阶段 01 遗留）、真实供应商连通性（无密钥）、阶段 02 的真实进程强杀恢复（阶段 02 遗留）、nginx 反向代理后的 SSE 断线重连整链。

## 工程入口

- 服务：
  - `backend/app/services/task_service.py`：项目/任务创建（幂等 + 同事务预留）、取消、重试、公司暂停；`ALLOWED_TRANSITIONS` 是唯一状态机定义。
  - `backend/app/services/execution_service.py`：领取、心跳、步骤、完成、过期租约恢复。
  - `backend/app/services/budget_service.py`：账户、预留、结算、释放、账本投影。
  - `backend/app/workers/task_worker.py`：Worker 进程（心跳 + 恢复 + 领取/执行循环 + 健康端点）。
  - `backend/app/workers/demo_processor.py`：**确定性演练处理器**（SUCCESS / TRANSIENT_FAILURE / PERMANENT_FAILURE / SLOW / UNKNOWN_USAGE / TIMEOUT）。
- 数据：`backend/app/models/tasks.py`（阶段 03 表）、`app/models/organization.py`（公司暂停列）、`alembic/versions/c41f7b93e2a5_phase_03_tasks_and_budget.py`。
- API：`backend/app/api/v1/tasks.py`（项目、任务、执行、预算、暂停、事件与 SSE）、`backend/app/api/schemas_tasks.py`。
- 前端：`src/views/ProjectsView.vue`、`TaskDetailView.vue`、`ExecutionDetailView.vue`、`BudgetsView.vue`、`OverviewView.vue`；`src/components/BudgetMeter.vue`；`src/api/task-types.ts`、`task-presentation.ts`（状态/账本中文标签唯一来源）；`src/layouts/AppShell.vue`（顶栏暂停）。
- 验收脚本：`scripts/verify_phase03_runtime.py`（真实进程）、`verify_phase03_compose.py`（双容器 + `docker kill`）、`verify_phase03_restart.py`（整栈停机重启）、`verify_phase03_migration.py`（迁移往返）、`verify_phase03_ui.cjs`（四视口真机 SPA），配套 `scripts/phase03_test_stack.ps1`。

### 检查命令

```powershell
# 后端（backend/，隔离库必须由 TEST_ADMIN_DATABASE_URL 指定）
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://<user>:<pass>@127.0.0.1:<port>/postgres'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m pytest -q

# 前端（frontend/）
pnpm check
```

### 启动与配置

```bash
python scripts/generate_secrets.py
docker compose up -d --build
```

新增/变更的配置项（值不进仓库）：

| 变量 | 默认 | 用途 |
|---|---|---|
| `WORKER_LEASE_SECONDS` | 30 | 租约时长；大于单个步骤耗时。验收用 12 |
| `WORKER_POLL_INTERVAL_SECONDS` | 1 | 空闲轮询间隔 |
| `DEMO_SLOW_STEP_SECONDS` | 1.5 | 演练慢步骤时长；上限 600，用于制造可观察的长执行 |
| `COMPANY_DEFAULT_BUDGET_TOKENS` | 1000000 | 新建公司账户的额度。真实可见、可改，不是「无限」 |
| `PROBE_RESERVATION_TOKENS` | 512 | 单次连通性探测的公司预留额 |

Compose 的 `worker` 命令已从阶段 01 的心跳循环改为 `python -m app.workers.task_worker`。隔离验收栈 `deploy/compose.phase03-test.yml` 额外定义 `worker2`（端口 9101，仅内部网络）。

## 变更

### 数据表（10 张新表）

| 表 | 关键约束 |
|---|---|
| `projects` | `(company_id,key)`、`(company_id,name)` 唯一；`run_mode IN ('DEMO','REAL')`；额度非负 |
| `tasks` | `(company_id,idempotency_key)` 唯一；状态枚举检查；`attempt_count`、`max_auto_retries`(≤5)、`fencing_counter` 非负 |
| `task_executions` | `(task_id,attempt)` 唯一；`fencing_token ≥ 1`；用量列可空且非负 |
| `task_execution_steps` | `(execution_id,sequence)` 唯一；`attempts ≤ 3` |
| `task_events` | 追加式时间线 |
| `domain_events` | 自增 ID 即 SSE 游标；`payload` 为 JSONB |
| `task_messages` | sender/receiver 类型受限；`correlation_id` 索引 |
| `budget_accounts` | `(scope,scope_id)` 唯一；limit/consumed/reserved 非负 |
| `budget_reservations` | open 行按 `(account_id,kind,owner_ref)` 部分唯一；`settled_tokens` 仅解析后存在 |
| `budget_ledger_entries` | `(account_id,entry_kind,reservation_id)` 唯一；`entry_shape` 约束禁止「结算但既无用量又未标记」 |

`companies` 增加 `paused_at`、`pause_reason`，`status` 扩为 `ACTIVE/PAUSED` 并新增 `pause_pairing`。

### API

新增（全部要求所有者会话；写操作另需 CSRF）：

```
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{id}
GET    /api/v1/tasks?projectId&status&limit&offset
POST   /api/v1/projects/{id}/tasks          # 需 idempotencyKey；重复返回 200
GET    /api/v1/tasks/{id}                   # 含 executions 与 events
POST   /api/v1/tasks/{id}/cancel            # 协作式
POST   /api/v1/tasks/{id}/retry
GET    /api/v1/tasks/{id}/events?after=     # 任务级事件补读
GET    /api/v1/executions/{id}
GET    /api/v1/budgets?ledgerLimit=
GET    /api/v1/budgets/pending-reconciliation
GET    /api/v1/company/state
POST   /api/v1/company/pause
POST   /api/v1/company/resume
GET    /api/v1/events/stream?after=&projectId=   # SSE，支持 Last-Event-ID
```

既有接口变化：`POST /api/v1/models/{id}/probe` 现在受公司暂停与公司额度约束，可能返回 `COMPANY_PAUSED` / `BUDGET_INSUFFICIENT`（`call_count=0`，不发请求）。其余阶段 02 接口未变。

### 状态机

```
QUEUED          → RUNNING | CANCELLED | BLOCKED
RUNNING         → SUCCEEDED | FAILED | CANCELLED | INTERRUPTED | BLOCKED | QUEUED(自动重试)
WAITING_APPROVAL→ QUEUED | CANCELLED            （阶段 06 使用，本阶段不进入）
BLOCKED         → QUEUED | CANCELLED
INTERRUPTED     → QUEUED | CANCELLED
FAILED          → QUEUED
SUCCEEDED / CANCELLED → 终态
```

### 兼容与回退

- 升级：先备份；`docker compose run --rm migrate` 后 head 应为 `c41f7b93e2a5`。既有公司会获得额度 0 的公司账户，**需要运营显式设置额度**否则新任务会被拒绝（这是刻意的失败，不是 bug）。
- 回退：`alembic downgrade 8c24a71d9e03` 删除全部阶段 03 表并把 `PAUSED` 复位为 `ACTIVE`；只在无任务数据时安全。
- 无 Python/前端依赖变化，镜像只需重建。

## 关键决定

见 [ADR-0004](../decisions/0004-phase-03-task-scheduling-and-budget.md)。要点：队列就是 `tasks` 表；fencing token 在 SQL 中单调自增；只有声明幂等的处理器才自动重放；未知用量消耗整笔预留并标记待核对，账本约束允许但强制标记；结算与预留都由数据库唯一索引兜底；`REAL` 被拒而不降级；阶段 02 探测纳入公司预算但建公司前不占额度；SSE 非 2xx 是致命错误，重连由前端负责。

## 验收

- 后端 **195 passed**、前端 **103 passed**；ruff/format/mypy 与 `pnpm check` 全绿。
- 真实进程 8 项、容器 4 项、整栈重启 4 项、迁移 5 项、UI 4 视口 40 张截图，全部通过。
- 未验证项见交接开头，与验收报告 §6 一致。

## 下一阶段接入点

阶段 04（隔离执行与工具网关）可以直接复用：

1. **执行内核**：`ExecutionService.claim_next/heartbeat/start_step/finish_step/complete/recover_expired_leases`。阶段 04 的真实执行器应作为新的“处理器”接入，**保持 fencing 写入**，并把 `is_idempotent()` 如实设为 `False`（真实副作用不可盲目重放），从而自动走“中断后等待人工重试”的路径。
2. **预算**：真实模型调用前用 `BudgetService.reserve_task_allowance` 持步骤预留，结束后 `settle`，未知用量传 `known=False`。**不要绕过 `BudgetService` 直接写 `budget_accounts`**，否则缓存与账本会漂移。
3. **事件**：新增事件类型时同步 `DomainEventType`、前端 `task-presentation.ts` 的 `EVENT_TYPE`，否则页面显示原始枚举。
4. **已知限制**：`budget_accounts.consumed_tokens/reserved_tokens` 是缓存值，账本是事实来源；目前没有对账任务，若怀疑漂移应比对两者。`WAITING_APPROVAL` 已在状态机里预留但无入口。
5. **建议先处理**：为 `budget_ledger_entries` 增加按公司与时间窗口的对账查询，供阶段 06 的审批预算预估使用；以及在 nginx 代理后补一次 SSE 断线重连的整链证据。

交接可在不读取聊天记录的情况下使用；阶段 04 未开始，本轮未推送、未发布、未做生产部署。
