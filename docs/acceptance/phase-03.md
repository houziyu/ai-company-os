# 阶段 03 验收报告

日期：2026-11-02（Asia/Shanghai）。对应阶段：03 持久任务调度与预算。
环境：Windows 10.0.26100、仓库既有 Python 3.13 / Node 24 / pnpm 10.34.9 及锁定依赖；隔离 PostgreSQL 17（`127.0.0.1:55433/acos`）与专用验收库；Docker 29.6.2 用于容器级证据。未升级任何依赖，未读写 `.env`，未改动既有 Compose 栈与其数据卷。
本报告结论：**通过（阶段 03 自身验收）；阶段 02 的三项外部验证缺口继续保留为未验证。**

> 阶段 02 遗留缺口（真实浏览器 200% 缩放、真实供应商连通性、真实进程强杀恢复）在本轮之前即未关闭。本轮**不**把它们改为通过；阶段 03 的「进程强杀恢复」是**独立的新证据**（见 §2.2/§2.3），不能替代阶段 02 的那一项结论。

## 1. 需求与证据

| 需求/验收 ID | 实际实现 | 测试方法与命令 | 实际结果 | 证据文件 |
|---|---|---|---|---|
| P3-1 九类实体与迁移 | `projects`/`tasks`/`task_executions`/`task_execution_steps`/`task_events`/`domain_events`/`task_messages`/`budget_accounts`/`budget_reservations`/`budget_ledger_entries`；公司 `PAUSED` | `alembic upgrade|downgrade|check`；`scripts/verify_phase03_migration.py` | 5 项通过：head `c41f7b93e2a5`、降级回 `8c24a71d9e03`、再升级保留阶段 02 的探测/用量/公司数据、`PAUSED` 可持久化、暂停列随降级移除 | `migration-process.txt`、`migration/migration-results.json` |
| P3-2 任务与执行分离、状态机受控 | 明确迁移表 `ALLOWED_TRANSITIONS`；API 不接受 `status`，只有取消/重试命令 | `pytest tests/test_tasks.py`（状态与终态断言）；`test_cannot_cancel_a_terminal_task` | 通过；对终态任务取消返回 409 `INVALID_TASK_TRANSITION` | `backend-targeted.txt` |
| P3-3 两 Worker 只允许一个持有者 | `SKIP LOCKED` + 条件 `UPDATE ... WHERE status='QUEUED'`，`fencing_counter` 在 SQL 自增 | `test_only_one_of_two_workers_claims_a_task`（2 线程/2 连接）；真实双进程 `two-worker-claim`；容器级 `container-claim` | 三处均为 1 个执行、token=1；真实进程胜者 `worker-2`（PID 40064），另一进程同等轮询但未领取 | `runtime-process.txt`、`compose-process.txt` |
| P3-4 旧租约不能写回 | 每次写入带 `fencing_token` 条件；不匹配抛 `LeaseLostError` | `test_old_lease_cannot_write_after_a_newer_claim` | 通过：旧执行保持 `INTERRUPTED`，任务保持新一轮 `RUNNING`，`fencing_counter` 单调递增；晚到结果被拒绝 | `backend-targeted.txt` |
| P3-5 预算并发不超支 | 固定锁序 + 条件扣减 + 非负检查约束 | `test_two_tasks_competing_for_scarce_project_budget_only_one_wins`；真实进程 `budget-race` | 通过：4 线程 1×201 + 3×409；真实双请求 `[201, 409]`，项目预留 700 ≤ 上限 1,000 | `runtime-process.txt` |
| P3-6 创建任务/预留/事件同事务 | 单事务写任务、双层预留、任务账户、时间线与领域事件 | `test_insufficient_project_budget_refuses_task`、`test_insufficient_company_budget_refuses_task_without_creating_rows` | 通过：拒绝后无任务、无预留、无账本行，账户未被留下占用 | `backend-targeted.txt` |
| P3-7 幂等与冲突 | `(company_id, idempotency_key)` 唯一 + 请求体摘要 | `test_duplicate_task_request_creates_one_task_and_one_reservation`、`test_same_idempotency_key_with_different_body_is_refused`；真实进程 `idempotent-create` | 通过：201→200 同一任务；异体 409 `IDEMPOTENCY_CONFLICT`；仅 1 任务、2 笔规划预留、2 条 RESERVE 账本 | `runtime-process.txt` |
| P3-8 未知用量保守预留并标记待核对 | 结算时 `usage_tokens=NULL`、`needs_reconciliation=true`、消耗整笔预留；账本检查约束允许该形状 | `test_unknown_usage_consumes_hold_and_is_flagged`；UI `task-unknown-usage-*` | 通过：任务账户消耗 128（两步各 64），公司消耗整包 1,000，所有结算行 `usageKnown=false` 且被标记 | `backend-targeted.txt`、`ui/task-unknown-usage-*.png` |
| P3-9 重复结算防护 | 结算前检查状态 + `(account_id, entry_kind, reservation_id)` 唯一 | `test_duplicate_settlement_does_not_double_count`、`test_successful_demo_run_settles_budget_exactly_once` | 通过：再次结算返回 `None`，公司已用不变，账本无重复键，无遗留 open 预留 | `backend-targeted.txt` |
| P3-10 暂停阻止新调用 | 公司 `PAUSED` 阻止建任务与领取；探测在本地被拒且 `call_count=0` | `test_company_pause_blocks_new_tasks_and_claims`、`test_company_pause_blocks_probes_without_spending`；真实进程 `pause-blocks` | 通过：建任务 409 `COMPANY_PAUSED`；探测 0 次 HTTP 调用；恢复后任务可建 | `runtime-process.txt` |
| P3-11 协作式取消 | 排队即取消并释放；运行中仅记录请求，由 Worker 在安全检查点停止 | `test_cancel_queued_task_releases_planning_holds`、`test_cancel_running_task_is_cooperative_and_settles_once` | 通过：排队取消后双层预留归零且有 2 条 RELEASE；运行中取消后状态仍 `RUNNING` 且 `cancelPending=true`，完成后为 `CANCELLED` 且未调用时消耗为 0 | `backend-targeted.txt` |
| P3-12 有限重试、独立执行 ID、历史保留 | 重试插入新执行并记录 `retried_from_execution_id`；只对可重试且幂等的失败生效 | `test_transient_failure_retries_with_a_new_execution_id`、`test_permanent_failure_is_not_retried`、`test_retry_after_failure_creates_new_attempt_and_keeps_history` | 通过：2 次执行不同 ID，失败尝试的步骤行保留；永久失败只执行 1 次 | `backend-targeted.txt` |
| P3-13 重启恢复、未知副作用不重放 | 租约过期为 `INTERRUPTED` 并置 `interrupted_unknown_side_effects`；仅声明幂等才自动重排 | `test_expired_lease_is_interrupted_and_requeued_for_idempotent_processor`、`test_interrupted_attempt_without_idempotency_waits_for_a_human`；真实进程 `worker-killed`/`idle-requeue`；容器 `container-killed`/`container-recovery`；整栈 `restart-running` | 全部通过（详见 §2） | `runtime-process.txt`、`compose-process.txt`、`restart-process.txt` |
| P3-14 阶段 02 探测纳入公司预算 | 探测的 `PENDING` 意图与公司 `PROBE` 预留同一短事务提交，结束后结算；建公司前不占预算 | `pytest tests/test_v2_fixes.py`、`tests/test_organization.py`、`tests/test_review_fixes.py` | 通过：原有 11 项 v2 修复与全部探测测试保持绿；新增暂停/额度不足的本地拒绝路径 | `backend-full.txt` |
| P3-15 SSE 事件 ID 与断线续读 | `domain_events` 自增 ID；`Last-Event-ID`/`?after=`；前端致命错误自行退避重连 | `test_event_stream_replays_after_last_event_id`、`test_sse_stream_emits_ids_and_resumes`、`phase03-views.spec.ts` 重连用例；真实进程 `sse-resume`；UI 四视口断线/恢复 | 通过：回放严格在给定 ID 之后；流帧含 `id:`；UI 在流被中断时显示「连接中断，结果可能未更新」并自动恢复 | `runtime-process.txt`、`ui/task-sse-disconnected-*.png` |
| P3-16 页面（看板/任务/执行/预算/概览） | 项目与任务看板、任务详情、执行详情、预算页、概览真实指标、顶栏暂停 | 真机 SPA + 真实 API + 真实数据库，四视口 | 通过（详见 §3） | `ui/ui-results.json` 与 40 张截图 |

### 检查命令与结果

| 命令（目录） | 结果 |
|---|---|
| `backend`：`python -m ruff check .` | All checks passed |
| `backend`：`python -m ruff format --check .` | 70 files already formatted |
| `backend`：`python -m mypy app` | 51 source files，无问题 |
| `backend`：`pytest -q` | **195 passed**（沿用阶段 02 的 170 + 新增 25），10 条依赖弃用警告，200.25 秒 |
| `frontend`：`pnpm check` | tokens / lint / typecheck / **103 passed**（原 93 + 新增 10）/ build 全通过 |
| 根目录：`python scripts/verify_phase03_migration.py` | 5 项通过 |
| 根目录：`python scripts/verify_phase03_runtime.py` | 8 项通过（真实 uvicorn + 两个真实 worker 进程） |
| 根目录：`python scripts/verify_phase03_compose.py` | 4 项通过（两个 worker 容器 + `docker kill`） |
| 根目录：`python scripts/verify_phase03_restart.py` | 4 项通过（整栈停机再启动） |
| 根目录：`node scripts/verify_phase03_ui.cjs` | 4 视口通过，40 张截图，0 页面异常 |

新增测试分布：`backend/tests/test_tasks.py` 25 项；`frontend/src/__tests__/phase03-views.spec.ts` 10 项。

## 2. 并发、租约与恢复（本阶段核心证据）

### 2.1 真实独立进程（`scripts/verify_phase03_runtime.py`）

一个真实 uvicorn API 进程 + **两个真实的 `app.workers.task_worker` 子进程**（不同 PID，各自健康端点），对隔离库 `acos_phase03_runtime`：

| 检查 | 观测 |
|---|---|
| `two-worker-claim` | 与任务同时启动的两进程竞争：`execution_count=1`，胜者 `worker-2`，`fencing_token=1`；另一进程（PID 49452）同条件轮询未领取 |
| `worker-killed` | 用 `taskkill /F /T` 终止持有租约的 worker 进程（PID 5832），`exit_code=1`、`still_alive=false`；被杀时执行 #2 处于 `RUNNING`，租约到期时间已记录 |
| `idle-requeue` | 存活 worker 在租约过期后恢复：执行 #2 → `INTERRUPTED`（`interruptedUnknownSideEffects=true`，恢复说明「租约过期；处理器步骤幂等且仍有重试次数，已重新排队。」），执行 #3（新 ID、attempt=2）完成，任务 `SUCCEEDED` |
| `budget-race` | 两个真实并发请求各要 700、项目上限 1,000：状态 `[201, 409]`，项目预留 700 |
| `ledger-invariants` | 账户无负数、无遗留 open 预留、`pendingReconciliationCount=0`；公司已用 6,000 |

### 2.2 容器级（`scripts/verify_phase03_compose.py`，项目 `acos-phase03-test`，端口 18085）

| 检查 | 观测 |
|---|---|
| `compose-scale` | 同一镜像起两个 worker 容器，各自 `healthy`；容器 ID 不同（`519c18…`、`db386d…`） |
| `container-claim` | 4 个真实任务参与，仅 1 个执行，胜者 `worker-1` |
| `container-killed` | `docker kill`（SIGKILL）持有租约的 `worker2` 容器：`kill_exit=0`、输出 `Killing / Killed`；被杀时执行 #2 `RUNNING` |
| `container-recovery` | 存活容器恢复：状态 `[INTERRUPTED, SUCCEEDED]`、attempt `[1, 2]`，最终 `SUCCEEDED` |

### 2.3 整栈重启（`scripts/verify_phase03_restart.py`）

| 检查 | 观测 |
|---|---|
| `restart-queued-before` | 停止 worker 后新建任务：`QUEUED`、0 执行——队列在数据库，不在进程内存 |
| `restart-inflight-stopped` | 45 秒慢步骤 + Docker 20 秒宽限期，`compose stop` 会在中途 SIGKILL；停止时执行 #8 `RUNNING` |
| `restart-queued` | 重新 `up` 后排队任务被执行（`worker-2`），`SUCCEEDED` |
| `restart-running` | 被中断的执行转为 `INTERRUPTED` 并重排，attempt `[1, 2]`，最终 `SUCCEEDED` |

> 说明（一）：第一次尝试本项时用了 20 秒慢步骤，正好等于宽限期，容器被优雅停止并**正常完成**——这验证了「优雅停机不产生中断」，但**没有**验证中断恢复。因此把步骤改为 45 秒并重跑，才有上表结果；`restart-process.txt` 记录的是最终一次。
>
> 说明（二）：§2.1 的 `two-worker-claim` 与 `worker-killed` 记录的 PID 不同是预期的——为了确定性地在「执行中」强杀，脚本在竞争检查之后**用长慢步骤重启了两个 worker**，再执行杀进程。重启前后是同一对 worker 身份（`worker-1`/`worker-2`），只是进程号变了。

## 3. UI 检查

真实构建产物 + 真实 API + 真实 PostgreSQL + 真实 worker，Playwright 驱动 Chrome；**不是模拟 API**。

- **视口**：1440×900、1024×768、390×844、360×800。每视口 10 张截图：`board`、`board-list`、`create-task-drawer`、`task-succeeded`、`task-failed`、`task-unknown-usage`、`execution-detail`、`budgets`、`overview`、`task-sse-disconnected`。共 40 张，均在 `evidence/phase-03/ui/`。
- **状态覆盖**：默认、空（空看板项目）、加载（骨架）、错误（看板/任务读取失败状态由组件单元测试与 `ErrorState` 覆盖）、禁用（终态任务不显示取消/重试）、成功/失败/未知用量/中断、SSE 断线。审批相关状态属于阶段 06，本阶段不显示。
- **键盘路径**：每视口用 Tab 聚焦「新建任务」→ Enter 打开抽屉 → 输入标题与目标 → Tab 到「创建任务」→ Enter 提交 → 跳转任务详情。四视口均成功（记录 `keyboardCreatedTask`）。
- **对比度**：从渲染后的计算样式测量状态徽章前景/背景，四视口最小 **4.79:1**（阈值 4.5:1）；徽章数量随页面 4–12 个。
- **布局**：四视口 `document.scrollWidth <= innerWidth + 1` 均成立；抽屉在手机端左右边界均在视口内。
- **SSE**：状态条（不是顶栏的 HTTP 就绪徽章）显示「已连接，实时更新中」→ 只中断事件流端点 → 显示「连接中断，结果可能未更新」→ 恢复端点后**不刷新页面**自动回到「已连接」。
- **语义**：看板卡片、任务状态、账单行均以中文文字表达状态，不依赖颜色；演练处理器在页面顶部与任务结果处均标注「演练运行（确定性处理器，非真实模型输出）」；预算页明确写出「不提供充值或在线改额度」。

### 本轮 UI 发现并修复的问题（截图驱动）

| 问题 | 现象 | 修复 |
|---|---|---|
| 看板空列挤占可视区 | 7 列 300px，前 4 列全空，真实卡片被推到屏幕外 | 只渲染有任务的状态列；空状态在工具条以「失败 0 · 已取消 0」文字说明 |
| 手机默认看板 | 违反 COMPONENTS.md「手机默认任务列表」 | 窄屏初始为列表，看板作为可选 |
| 步骤表在桌面被截断 | 7 列硬最小宽导致「输出」列被裁 | 表格改为自适应宽度 + 允许换行；手机断点改为带标签的卡片堆叠 |
| 预留/释放行显示「待核对」 | 误把「无用量」当成「用量未知」 | 新增 `formatLedgerUsage`：只有 SETTLE 行才可能是待核对 |
| 概览页首屏长期只有骨架 | 指标数据与主加载串行 | 阶段 03 指标改为独立加载，失败时指标保持「—」而不阻塞页面 |
| 概览阻塞面板出现空子弹点 | 空 `<li>` | 合并为单一条件 |
| SSE 断线后永不恢复 | 代理 502 是 EventSource 的致命错误，浏览器不再重连 | 前端自行指数退避重连并按最后事件 ID 续读，附回归测试 |
| 公司未创建时 Worker 反复重启 | `claim_next` 抛 `CompanyNotFoundError` | 视为「暂无可领取的工作」，返回空 |

以上均为真实缺陷，修复后重新构建并重跑四视口验收。

## 4. 数据与安全

- **迁移**：只新增迁移 `c41f7b93e2a5`，未改写 `6a790d8c2b88`、`7b41c9d2f0aa`、`3f8d2c6a91b4`、`8c24a71d9e03`。`alembic check` 无漂移。往返实测保留阶段 02 的探测、用量与公司行（21/7 Token 不变）。
- **降级边界**：降级删除阶段 03 全部表，并把 `PAUSED` 复位为 `ACTIVE`；只在无任务数据的库上安全。已在专用库实测。
- **现有公司预算账户**：迁移为既有公司补建额度 0 的 `COMPANY` 账户。这是刻意的：不虚构「无限」，也不静默放开花费。
- **幂等**：任务创建按 `(company_id, idempotency_key)` + 内容摘要；预留在 open 状态下按 `(account_id, kind, owner_ref)` 唯一；结算按 `(account_id, entry_kind, reservation_id)` 唯一。三者都在数据库层。
- **服务端强制**：状态只能由服务命令推进；额度、暂停、租约全部在服务端校验；前端隐藏按钮不作为控制。`REAL` 运行模式在服务端被拒，不降级。
- **秘密**：本阶段没有新增任何密钥入口。演练处理器不调用任何外部服务；探测仍只经适配器。UI 无密钥字段。
- **隔离**：测试只使用 55433 上的随机克隆库与三个专用库（`acos_phase03_runtime`、`acos_phase03_migration`、`acos_phase03_ui`）；容器证据使用项目 `acos-phase03-test` 的独立卷。未触碰 `.env`、开发卷或既有 Compose 栈。
- **数据保留**：任务、执行、步骤、事件、消息与账本均无级联删除；所有外键 `RESTRICT`。

## 5. 模型验证

| 类别 | 调用数 | 说明 |
|---|---|---|
| 演练处理器（确定性） | 全部本阶段任务执行 | 固定两步、固定 Token 数值；标签「演练运行」出现在任务、执行与页面 |
| 协议模拟（MockTransport） | 阶段 02 探测测试沿用 | 配置竞态、Unicode、用量、失败、恢复 |
| **真实供应商 API** | **0 次，未执行** | 无密钥。真实模式在本阶段被服务端拒绝，不会被静默降级为演练 |

本阶段没有把任何演练结果表述为真实模型产出。

## 6. 未通过与未执行

| 项 | 状态 | 原因 / 影响 |
|---|---|---|
| 真实浏览器 200% 缩放 | 未验证（阶段 01 遗留） | 与阶段 02 记录相同；本机输入注入通道受阻，等效布局门禁不能替代 |
| 真实供应商连通性 | 未执行 | 无密钥；不阻塞本阶段工程结论 |
| 阶段 02 的真实进程强杀恢复 | 未验证（阶段 02 遗留） | 本轮新增的是**阶段 03 任务队列**的强杀证据，不能回溯替代阶段 02 的结论 |
| 完整 Compose 端到端（含 web 反向代理下的 SSE） | 未执行整链 | UI 验收用真实 API 直连 + 静态托管代理 `/api`；容器栈验证了 worker/api/DB，未在 nginx 代理后重跑 SSE 断线 |
| 真实支付/币值折算 | 不适用 | 本阶段只做 Token，禁止把估算当账单；未配置价格 |
| 多公司 / 多租户 | 不适用 | 产品基线为单公司 |
| 阶段 04 隔离执行器 | 不在范围 | 未实现，未被调用 |

## 7. 验收判断

阶段 03 的退出条件逐项满足：真实 PostgreSQL 队列、双 Worker 领取唯一性、租约与 fencing、真实进程退出后的中断与恢复、预算并发不超支、幂等冲突、暂停阻止新调用、协作式取消、有限重试与新执行 ID、重复结算防护、SSE 断线补读、迁移往返与既有回归，均有本轮实际执行的证据。

**可以交接阶段 03。** 阶段 02 的三项外部验证缺口状态不变；它们不阻塞阶段 03 的实现结论，但在关闭前不应把阶段 02 标为完整通过。未推送、未发布、未做生产部署。

同步证据索引：[`evidence/phase-03/README.md`](evidence/phase-03/README.md)。交接：[`../handoff/phase-03.md`](../handoff/phase-03.md)。
