# 阶段 03 独立审查 R1–R13 修复验收

日期：2026-11-02（Asia/Shanghai）
对应阶段：03 持久任务调度与预算（修复轮，不进入阶段 04）
代码版本：`main`，基准提交 `0b868ca8cbe58e6ad95644d0f6d7e7db93e5701e` 之上的工作区（含原阶段 03 未提交改动 + 本轮修复）
环境：Windows 10.0.26100；Python 3.13.12；PostgreSQL 17.11（独立容器 `acos-phase03-fix-20261102`，仅映射 `127.0.0.1:55440`）；Node 24 / pnpm 10.34.9；nginx `nginxinc/nginx-unprivileged:1.27-alpine`
**本报告结论：13 项全部已修复并验证。** 原审查的 13 个诊断场景在修复后全部失败（即缺陷不再存在）；34 项正确行为回归、229 项后端全量、110 项前端测试、迁移往返、双 Worker/真实进程强杀/nginx 代理后 SSE 断线补读、四视口 UI 验收均通过。真实模型调用未执行（无密钥）。

## 一、结论速览

| 项 | 修复前（原审查） | 修复后（本轮实测） | 结论 |
|---|---|---|---|
| 原诊断 `test_review_repros.py` | 13 passed（= 13 个缺陷复现） | **13 failed**（缺陷已不存在，断言不再成立） | 反证成立 |
| 新增正确行为回归 `tests/test_phase03_review_fixes.py` | 不存在 | **34 passed** | 通过 |
| 后端全量 | 195 passed（原阶段 03） | **229 passed** | 通过 |
| 后端静态检查 | 通过 | ruff / format / mypy 全通过（73 files formatted, 52 source files） | 通过 |
| 前端 `pnpm check` | 103 passed | **110 passed** + lint/typecheck/build | 通过 |
| 迁移 | head `c41f7b93e2a5` | head **`e5b7c9d1a2f4`**；升级/降级/再升级与数据保留 PASS | 通过 |
| 真实进程（uvicorn + 2 workers，OS 强杀） | 8 项 | **10 项**，含两个新增修复项 | 通过 |
| nginx 代理后 SSE 断线补读 | 未执行 | **3 项 PASS**（真实 `deploy/nginx/default.conf`） | 通过 |
| UI 四视口 + 键盘路径 | 40 张截图 | **40 张新截图，0 页面异常** | 通过 |
| 真实模型调用 | 未执行 | **未执行**（无密钥） | 未执行 |

## 二、R1–R13 逐项

通用说明：原审查的 `docs/acceptance/evidence/phase-03-review/test_review_repros.py` **保持原样**，作为“缺陷曾经存在”的历史证据。它现在失败正是修复生效的证明（见 `evidence/phase-03-fixes/review-diagnostics-after-fix.txt`）。正确行为由新文件回归覆盖。

---

### R1 · P1 已领取任务绕过公司暂停和取消检查 — 已修复并验证

**原因**：`start_step()` 只校验 fencing，不读公司状态与取消标记；处理器每步前也不补检，`SUCCESS` 路径从不读取消标记。

**修改位置**
- `backend/app/services/execution_service.py`：新增 `_assert_startable()`（在持有 task→execution 行锁的事务内读取**数据库中的**公司状态与 `cancel_requested_at`），由 `start_step()` 在创建步骤与预留**之前**调用；新增 `ExecutionStoppedError`。
- `backend/app/workers/demo_processor.py`：`start_step` 的三种拒绝（停止/失租/额度不足）分别转为明确结果，不再把所有异常折叠成“失租”。
- `backend/app/domain/errors.py`、`domain/enums.py`：新增停止原因枚举。

**修复前失败证据**：`test_running_task_ignores_stop_gate[pause|cancel]` 复现：领取后暂停/取消，仍启动两步并 `SUCCEEDED`。

**修复后测试**（`tests/test_phase03_review_fixes.py`）
- `test_r1_claimed_but_not_started_task_honours_stop_command[pause|cancel]`：零步骤；暂停→任务 `INTERRUPTED` 且 `interrupted_unknown_side_effects=false`；取消→`CANCELLED`；三层账户已用/预留均为 0。
- `test_r1_pause_between_steps_stops_before_the_next_call`：仅步骤 0 存在，任务账户只留步骤 0 的 100 预留。
- 真实进程：`stop-gate-after-claim` 通过（步骤数 1、状态 `INTERRUPTED`、公司预留 0）。

**实际结果**：通过。**剩余风险**：暂停不中止已在执行的单个调用（协作式语义，ADR-0004 明确）；停止点是“步骤边界”，不是任意时刻。

---

### R2 · P1 过期租约仍可写入并被心跳重新延长 — 已修复并验证

**原因**：心跳、步骤 fencing、完成检查只看 token/status，不看 `lease_expires_at`。

**修改位置**：`execution_service.py`：`heartbeat()` 的条件增加 `lease_expires_at > now`；`_lock_fenced_execution()` 对所有步骤/完成写入统一校验未过期，过期即 `LeaseLostError`。

**修复前证据**：`test_expired_lease_can_write_and_renew`。

**修复后测试**：`test_r2_expired_lease_cannot_write_or_be_renewed`（写入被拒、心跳返回 False、租约仍为过去、无步骤行）；`test_r2_late_write_after_recovery_is_refused`（恢复后旧 token 零写入、无 open 预留）。

**实际结果**：通过。**剩余风险**：依赖数据库服务器时钟一致性（同一实例内一致）；极端网络延迟可能使合法心跳被判失租——行为是“放弃尝试”，不会重复扣费。

---

### R3 · P1 步骤 fencing 检查与写入之间存在竞争窗口 — 已修复并验证

**原因**：`_assert_fence()` 是无锁 `SELECT`，判定与后续写入分离；恢复可在两者之间提交 `INTERRUPTED`。

**修改位置**：`execution_service.py`：删除无锁 `_assert_fence`，改为 `_lock_fenced_execution()` 先 `SELECT ... FOR UPDATE` 锁 task、再锁 execution，判定与写入同事务；`recover_expired_leases()` 采用**相同锁序**（company → task → execution）并在锁内二次校验状态与租约。

**修复前证据**：`test_fence_check_does_not_protect_following_write`（在检查后插入恢复，旧调用仍写入 `RUNNING` 步骤与预留）。

**修复后测试**
- `test_r3_recovery_waits_for_an_in_flight_step_transaction`：用可控时钟让租约在事务进行中过期，恢复线程必须阻塞在行锁上（负断言），提交后由恢复接管：步骤恰好 1 条、`INTERRUPTED`、无 open 预留、任务账户保守消耗 10。
- `test_r3_stale_worker_cannot_write_after_recovery`：恢复提交后旧 token 的 `finish_step` 被拒，步骤保持 `INTERRUPTED`，无步骤级 SETTLE。
- 双 Worker 容器/进程：`two-worker-claim`、`worker-killed`、`idle-requeue` 通过。

**实际结果**：通过。**剩余风险**：锁序是**约定 + 实现**，由本轮代码统一；未来若新增第三个写入者，必须复用 `_lock_fenced_execution`（已在模块 docstring 写明约定）。

---

### R4 · P1 恢复时没有重试额度会使 Worker 异常退出 — 已修复并验证

**原因**：恢复先保守结算旧预留，再**无保护地**申请新预留；`BudgetInsufficientError` 越过 `WorkerRuntime.recover()`（只捕获 `SQLAlchemyError`），整批回滚。

**修改位置**
- `execution_service.py::recover_expired_leases()`：先锁 company；逐条 `_recover_one()`，业务错误被吸收为该条跳过；新预留放在 **savepoint** 内，失败即回滚该 savepoint 并把任务记为 `BLOCKED`（中断与保守结算保留）。
- `workers/task_worker.py::recover()`：捕获 `Exception` 并记录，不中断主循环。

**修复前证据**：`test_recovery_without_retry_budget_escapes_worker`（异常逃出，任务与执行仍 `RUNNING`）。

**修复后测试**
- `test_r4_recovery_without_retry_budget_blocks_without_crashing`：返回而非抛出；任务 `BLOCKED` + `BUDGET_INSUFFICIENT`；执行 `INTERRUPTED`；公司已用 1000、预留 0；无 open 预留；无 `recovery-retry` RESERVE 账本项；该任务不会被再次领取。
- `test_r4_recovery_without_any_call_is_not_charged`：从未调用时释放封套并成功重新排队（预留恢复为 1000）。
- 真实进程：强杀后 `idle-requeue`（`attempts [1,2]`，`INTERRUPTED → SUCCEEDED`）。

**实际结果**：通过。**剩余风险**：`BLOCKED` 需要人工追加额度或重试；不会自动恢复——这是刻意的诚实状态。

---

### R5 · P1 取消后 Worker 丢失会释放可能已花费的公司/项目额度 — 已修复并验证

**原因**：恢复把未完成调用判为未知，却仅因存在取消标记就 `release_planning_holds()`。

**修改位置**：`execution_service.py`：新增 `_call_evidence()`（步骤行 + CALL 预留证据）与 `_close_envelope()`；仅当**证据证明从未启动调用**时释放，否则保守结算并标记待核对。`task_service.py` 的即时取消路径复用同一判定。

**修复前证据**：`test_cancelled_recovery_releases_unknown_company_cost`（任务账户 200，公司/项目归零）。

**修复后测试**：`test_r5_cancel_after_call_started_keeps_company_cost_conservative`：任务 200、公司/项目各保守消耗 1000、无 open 预留、三笔 SETTLE 均为 `usage_tokens=NULL + needs_reconciliation`。

**实际结果**：通过。**剩余风险**：保守消耗按“整笔规划封套”计，可能高于真实花费；已标记待核对供人工修正。

---

### R6 · P1 失败步骤的未知用量被汇总成“已知” — 已修复并验证

**原因**：失败分支返回前面成功步骤累计的 `usage_known`/Token，未纳入当前失败步骤的 `known=False`。

**修改位置**
- `demo_processor.py`：失败分支将 `usage_known=usage_known and not step_unknown`，Token 在未知时置 `None`。
- `execution_service.py::complete()`：新增 `_step_usage()`，**从步骤证据重建**尝试用量；任一 `RUNNING` 或 `usage_known=false` 步骤即整次尝试未知（不把已知部分当总数）。

**修复前证据**：`test_failed_step_unknown_usage_becomes_known_execution`（执行 `usage_known=True`，公司/项目仅记 200）。

**修复后测试**：`test_r6_failed_step_unknown_usage_makes_the_attempt_unknown`（执行未知+待核对、`settled_tokens=NULL`、任务 264、公司/项目各 1000 且 SETTLE 未标已知）；`test_r6_mixed_known_and_unknown_steps_keep_the_unknown_part`（TIMEOUT 混合场景同样未知）。

**实际结果**：通过。**剩余风险**：未知用量只能人工核对；系统不会猜测具体值。

---

### R7 · P1 首次创建任务可忽略配置的公司预算上限 — 已修复并验证

**原因**：`TaskService()` 自建 `BudgetService()`，使用硬编码 1,000,000，而非 `COMPANY_DEFAULT_BUDGET_TOKENS`。

**修改位置**
- `task_service.py`：`TaskService`/`ProjectService` 接受注入的 `budget_service`。
- `api/v1/tasks.py::_task_service()`：由 `request.app.state.budget_service` 构造，与预算页、探测共用同一实例。

**修复前证据**：`test_task_creation_ignores_configured_zero_budget`（配置 0，首任务仍 201，账户额度 1,000,000）。

**修复后测试**：`test_r7_first_task_uses_the_configured_company_default[0|7777]`（0 → 409 `BUDGET_INSUFFICIENT`、无任务/预留；7777 → 账户额度 7777 且预留 500）；`test_r7_budget_page_first_and_task_first_agree` 与 `test_r7_task_first_then_budget_page_agree`（两种请求顺序结果一致）。

**实际结果**：通过。**剩余风险**：既有公司沿用其账户既有额度；管理员修改配置不会追溯改写已存在账户。

---

### R8 · P2 SSE 用自增 ID 当提交顺序，可能永久漏事件 — 已修复并验证

**原因**：主键在事务**提交前**分配；较小 ID 的事务可较晚提交，流推进游标后永不回看。

**修改位置**
- `backend/app/services/event_feed_service.py`（新增）：`published_seq` 只分配给**发布者可见（即已提交）**的行；事务级 advisory lock 串行化发布；唯一部分索引防重复；`read_after` 按游标读取。
- `api/v1/tasks.py`：SSE 与 `/tasks/{id}/events` 都先 `publish_pending` 再按游标读；`id:` 使用游标。
- `backend/alembic/versions/e5b7c9d1a2f4_phase_03_publication_cursor.py`：加列 + 按 id 顺序回填 + 唯一/部分索引。

**修复前证据**：`test_concurrent_commits_skip_sse_event`（先提交 5，再提交 4，`after=5` 查不到 4）。

**修复后测试**
- `test_r8_late_committed_event_is_not_skipped`：两个真实会话反序提交；首读只发高 id，晚提交事件获得**更高游标**并在下一批补发，且不重复。
- `test_r8_cursor_is_stable_for_already_committed_events`（发布幂等、游标唯一）。
- `test_r8_http_replay_uses_the_publication_cursor`。
- nginx 真实代理：`through-nginx`、`resume-behind-nginx`（代理停机期间提交事件，恢复后补发）、`no-duplicates`。

**实际结果**：通过。**剩余风险**：未发布的已提交事件在**下一次读取/轮询**时才获得游标（最多一个轮询间隔，默认 1s）；进程若在业务提交后、发布前死亡，事件仍会在下次读取时发布，不丢。历史事件回填保持 id 顺序，旧 `Last-Event-ID` 可继续使用。

---

### R9 · P2 自动重试的两层预留不是原子操作 — 已修复并验证

**原因**：公司预留成功后项目预留失败，被宽泛 `except Exception` 吞掉转 `BLOCKED`，公司预留未撤。

**修改位置**：`execution_service.py::complete()` 与 `recover_expired_leases()`、`task_service.py::create()/request_retry()`：两层预留放入同一 `session.begin_nested()` savepoint；只捕获 `BudgetInsufficientError`。

**修复前证据**：`test_auto_retry_leaks_partial_company_hold`（任务 `BLOCKED` 但遗留 1000 公司 `RESERVED`）。

**修复后测试**：`test_r9_auto_retry_leaves_no_partial_company_hold`（任务 `BLOCKED`、无 open 预留、双方预留 0、无 `auto-retry` RESERVE 账本）；`test_r9_manual_retry_leaves_no_partial_hold_when_project_is_short`（手动重试同规则）。

**实际结果**：通过。**剩余风险**：`BLOCKED` 需人工处理；不会自动降额重试。

---

### R10 · P2 待核对查询过滤错状态 — 已修复并验证

**原因**：概览总数、账户计数、明细接口都限定 `status=RESERVED`，排除了 `SETTLED + needs_reconciliation=true`。

**修改位置**：`budget_service.py`：`account_view` 与新增 `pending_reconciliation()` 只按 `needs_reconciliation` 过滤（不按状态）；`api/v1/tasks.py` 三个读出口统一使用；`PendingReconciliationEntry` 增加 `status` 字段，供页面区分“已保守结算”与“尚未结束的调用”。

**修复前证据**：`test_unknown_reservations_invisible_in_reconciliation_api`（4 条待核对，API 全为 0/空）。

**修复后测试**：`test_r10_settled_unknown_usage_is_visible_in_every_projection`（总数 4、公司 1、项目 1、明细 4 条均为 `SETTLED`、三 scope 齐全）；`test_r10_open_unknown_hold_is_distinguished_from_settled`（`CALL + RESERVED`）。

**实际结果**：通过。UI 截图 `budgets-1024x768.png` 可见“待核对用量”8 行与账本一致。**剩余风险**：无自动核对工作流（阶段 04+）；页面只读。

---

### R11 · P2 取消中断/阻塞任务后永远无法到达取消终态 — 已修复并验证

**原因**：只有 `QUEUED` 直接取消，其余非终态一律被假设有 Worker 收尾。

**修改位置**：`task_service.py::request_cancel()`：按状态机判定——`QUEUED` 立即取消；`RUNNING` **且有存活租约执行**才协作式；其余非终态（`INTERRUPTED`/`BLOCKED`/`WAITING_APPROVAL`）立即取消并按调用证据解析剩余封套（新 `_resolve_envelope_on_cancel`）。

**修复前证据**：`test_cancelling_interrupted_task_never_completes`（200 但状态不变、重试永久 409）。

**修复后测试**：`test_r11_cancel_interrupted_task_reaches_cancelled`（`CANCELLED`、有 `finished_at`、无调用则释放封套、有 `TASK_CANCELLED` 事件）；`test_r11_cancel_blocked_task_reaches_cancelled`；`test_r11_cancel_running_task_stays_cooperative`（不回归：立即状态仍 `RUNNING`、`cancelPending=true`、执行写入标记）。

**实际结果**：通过。真实进程 `cancel-running-flag` 通过（即时 `RUNNING` → 最终 `CANCELLED`）。**剩余风险**：`RUNNING` 但租约已过期的任务被视为“无人收尾”，立即取消；若原 Worker 之后复活，其写入会被 R2/R3 拒绝。

---

### R12 · P2 查询参数命名不一致，项目筛选被静默忽略 — 已修复并验证

**原因**：FastAPI 独立 Query 参数不吃 `alias_generator`，前端发 `projectId`/`ledgerLimit`，后端只认 snake_case。

**修改位置**：`api/v1/tasks.py`：`ProjectIdQuery`/`LedgerLimitQuery` 使用 `AliasChoices("projectId","project_id")` 等，覆盖 `/tasks`、`/budgets`、`/events/stream`；两套拼写都接受，不再静默忽略。

**修复前证据**：`test_frontend_query_names_are_ignored`（`projectId` 返回两个项目；`ledgerLimit=1` 返回 4 条）。

**修复后测试**：`test_r12_frontend_query_names_filter_and_limit`（camel 与 snake 均只返回目标项目；`ledgerLimit=1` → 1 条、`ledger_limit=2` → 2 条）；`test_r12_sse_accepts_the_frontend_project_parameter`（`projectId` 过滤生效）。

**实际结果**：通过。**剩余风险**：同时传两套拼写时以 Pydantic 的 `AliasChoices` 顺序为准（camel 优先）；未定义为错误。

---

### R13 · P2 前端失败重提会生成新幂等键 — 已修复并验证

**原因**：`submitTask()` 每次点击无条件 `crypto.randomUUID()`；失败后保留草稿，下一次点击合法创建第二个任务。

**修改位置**
- `frontend/src/api/task-submission.ts`（新增）：`idempotencyKeyFor(current, body)` 按**请求体摘要**复用/更新键；`clearSubmissionKey()` 仅在确认成功或用户明确新建时调用。
- `frontend/src/views/ProjectsView.vue`：键绑定到表单提交生命周期；失败保留键（含网络错误/502），`IDEMPOTENCY_CONFLICT` 时换键；服务端返回 `created=false` 时仍进入同一任务详情页（说明“已创建过”而非重复创建）。

**修复前证据**：原审查为静态代码路径（未做浏览器断网复现）；本轮补充前后端两层回归。

**修复后测试**
- 前端 `src/__tests__/phase03-idempotency.spec.ts`（7 项）：同体复用键、改体换键、清空后新键、摘要稳定；组件级：失败后原样重提**同键**、编辑后**换键**、成功后新提交**换键**。
- 后端 `test_r13_same_key_after_lost_response_yields_one_task_and_one_hold`（同键同体重提 → 200、一个任务、两笔规划预留、一个任务账户）；`test_r13_different_body_under_the_same_key_is_refused`（409）。

**实际结果**：通过。**剩余风险**：未做真实浏览器断网（offline 事件）复现；覆盖为逻辑层 + 服务端契约，已在“未执行”列明。

---

## 三、新增测试如何覆盖原审查遗漏的边界

原审查主要证明“缺陷可被触发”。本轮新增 34 项后端 + 7 项前端测试补齐了它没有覆盖的边界：

1. **领取后、步骤之间的暂停与取消**（R1）：原审查只覆盖“领取后”，新增覆盖“第一步已提交、第二步之前”的中间点。
2. **恢复与步骤写入的确定性交错**（R3）：用可控时钟 + `threading.Event` 建立同步点，恢复线程必须阻塞在行锁；不靠 `sleep` 碰运气。
3. **恢复时额度不足不得遗留单层预留、不得拖垮其他任务**（R4/R9）：同时断言“中断已提交”“保守结算已提交”“无孤儿预留”“无失败重试的 RESERVE 账本”。
4. **已知与未知混合、取消+进程中断、重复结算**（R5/R6）：三层账本按各自口径断言（任务按步估算、公司/项目按封套），并验证 SETTLE 幂等唯一。
5. **相反顺序提交的 SSE 不漏事件**（R8）：两个真实数据库会话反序提交，断言晚提交事件获得更高游标并被补发。
6. **公司默认额度 0、非默认值、不同请求顺序**（R7）：三种配置 × 两种顺序。
7. **前端实际参数的多项目筛选与账本查询**（R12）：直接使用 `endpoints.ts` 的拼写。
8. **服务端已提交但客户端丢响应后原样重提**（R13）：前端逻辑层同键断言 + 服务端“一个任务/一对应预留”断言。
9. **停机期间提交 + nginx 代理后恢复**（R8）：真实 nginx 容器，代理被停止期间写入事件，重启后补发。

## 四、回归与真实运行验证

| 检查 | 命令 | 实际结果 | 证据 |
|---|---|---|---|
| 原审查诊断（修复前基线） | `pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-review/test_review_repros.py` | **13 passed**（缺陷存在） | `evidence/phase-03-fixes/baseline-defects-reproduced.txt` |
| 原审查诊断（修复后） | 同上 | **13 failed**（缺陷不再成立） | `evidence/phase-03-fixes/review-diagnostics-after-fix.txt` |
| 新增正确行为回归 | `pytest tests/test_phase03_review_fixes.py -v` | **34 passed** | `evidence/phase-03-fixes/regression-suite.txt` |
| 后端全量 | `pytest -q` | **229 passed** | `evidence/phase-03-fixes/backend-full.txt` |
| 后端静态 | `ruff check .` / `ruff format --check .` / `mypy app` | 全部通过（73 files formatted；52 source files） | `evidence/phase-03-fixes/backend-static.txt` |
| 前端完整 | `pnpm check` | **110 passed**，lint/typecheck/build 通过 | `evidence/phase-03-fixes/frontend-check.txt` |
| 迁移往返 | `python ../scripts/verify_phase03_fix_migration.py` | **PASS**（保留 2 行、回填游标单调、降级→再升级保留历史与顺序） | `evidence/phase-03-fixes/migration-roundtrip.txt` |
| 真实进程 | `python ../scripts/verify_phase03_runtime.py` | **10/10 通过**：双 Worker 竞争、`taskkill /F` 强杀、中断后重排完成、预算竞争、幂等、暂停阻断、SSE 续读、领取后暂停、运行中取消、账本不变量 | `evidence/phase-03-fixes/runtime/runtime-results.json` + `runtime/process-logs/` |
| nginx 后 SSE | `python scripts/verify_phase03_nginx_sse.py` | **3/3 通过**：经真实 nginx 配置收事件、代理停机期间提交、恢复补发、无重复 | `evidence/phase-03-fixes/nginx-sse/results.json` + `nginx-sse-run.txt` |
| UI 四视口 | `node scripts/verify_phase03_ui.cjs`（`PHASE03_UI_OUT` 指向本轮） | **passed=true**，4 视口、0 页面异常、40 张截图 | `evidence/phase-03-fixes/ui/ui-results.json`、`ui/*.png`、`ui-run.txt` |

真实进程与 nginx 验证期间另起独立数据库（`acos_runtime`、`acos_sse`、`acos_ui`），全部位于独立容器内；未使用开发/生产数据。

## 五、UI 检查

本轮修改了 `ProjectsView.vue`（R13 键生命周期）与 `BudgetsView` 依赖的接口字段（R10 新增 `status`），因此重跑真实浏览器验收：

- 视口：1440×900、1024×768、390×844、360×800；每视口 10 张截图，共 40 张，位于 `evidence/phase-03-fixes/ui/`。
- 状态始终以文字表达（“已通过/失败/已取消”），不仅靠颜色；颜色对比度由脚本按 4.5:1 断言。
- 键盘路径：`Tab` 到“新建任务” → `Enter` 打开抽屉 → 完整填写 → `Enter` 提交 → 跳转任务详情成功；四视口均生成键盘创建的任务。
- 窄屏：抽屉完全位于视口内，无横向溢出；列表视图为手机默认。
- R10 投影：预算页“待核对用量”显示 8 行，与账本一致（修复前为 0 行）。
- **未执行**：真实浏览器 200% 缩放（阶段 01 遗留缺口，本轮未重试，原因见 `evidence/phase-02/zoom-200-real.json`）。

人工检查：`board-1440x900.png`、`create-task-drawer-360x800.png`、`budgets-1024x768.png` 已实际查看，未见布局溢出、标签缺失或颜色单独承载语义。

## 六、数据与安全

- 独立 PostgreSQL 17 容器 `acos-phase03-fix-20261102`，仅映射 `127.0.0.1:55440`；未使用 Compose 开发栈、未读取 `.env`、未触碰用户数据库。测试 fixture 为每例创建随机克隆库并自行删除。
- 新迁移 `e5b7c9d1a2f4` **只新增列与索引**，按 id 顺序回填；升级/降级/再升级与数据保留已实测（`migration-roundtrip.txt`）。**回退限制**：降级 `e5b7c9d1a2f4 → c41f7b93e2a5` 会删除 `published_seq` 与相关索引，SSE 回退为按 id 排序的旧行为（可能重现 R8 的漏事件）；不删除任何业务行。降级到 `8c24a71d9e03` 仍会删除阶段 03 全部表（原有限制，未改变）。
- 预算不变量：所有真实运行后 `negative_accounts=0`、终态任务无 open 预留、未知用量 SETTLE 全部带标记。
- 幂等：创建任务与自动/手动重试的“两层预留”均为单一 savepoint 单元。
- 权限与秘密：本轮未改授权模型，未引入密钥；未新增对外网络调用。

## 七、模型验证

- 演练处理器：本轮全部业务路径验证均使用确定性 DEMO 处理器，页面/执行记录明确标注“演练运行（确定性处理器，非真实模型输出）”。
- 模拟 API：未新增。
- **真实 API：未执行**（无真实供应商密钥）。阶段 02 的真实 DeepSeek 连通性缺口保持未验证；本轮不以任何模拟结果替代。

## 八、未通过与未执行

**仍失败：无。**

**未执行（不得记为通过）**
1. 真实模型供应商调用（无密钥）。
2. 真实浏览器 200% 缩放（阶段 01 遗留；本轮未重试）。
3. 阶段 02 的真实进程强杀恢复（本轮新增的强杀证据属于阶段 03，不能回溯替代）。
4. R13 的真实浏览器断网（offline）复现：覆盖为前端逻辑测试 + 服务端契约测试。
5. 完整 Compose 栈（web+api+worker+postgres）的整链 nginx SSE：本轮用真实 nginx 配置 + 真实 uvicorn + 独立数据库验证，未起整套 Compose。
6. 生产数据迁移演练：只在空库与夹具库上执行；既有生产库升级前仍需备份（迁移仅加列/索引/回填，风险低但未对真实数据执行）。

**受阻**：无。

**遗留风险汇总**
- 停止点是步骤边界；进行中的单次调用不会被强制中止（协作式，符合 ADR-0004）。
- 保守结算可能高估花费，需人工核对；系统提供待核对列表但不自动修正。
- 事件发布最多延迟一个轮询间隔（默认 1s）。
- `BLOCKED`/`INTERRUPTED` 需要人工介入，不会自动无限重试。
- 暂停期间已领取但未开始的尝试会停在 `INTERRUPTED`，恢复公司后需人工重试（不会自动续跑）。

## 九、对历史证据的影响（如实记录）

- 原审查报告、交接与诊断证据**保持原样**，未修改。
- 重跑 UI 脚本时，脚本默认输出目录曾指向 `docs/acceptance/evidence/phase-03/ui`，导致该目录下的历史截图与 `ui-results.json` 被本轮同内容结构的新截图覆盖；该目录未被版本控制，无法恢复旧文件。本轮已把脚本输出改为可配置（`PHASE03_UI_OUT`），并把本轮证据写入 `evidence/phase-03-fixes/ui/`。历史 `phase-03.md` 中的结论本身未被改写，但其截图证据现在对应 2026-11-02 15:49 的修复后运行，需以此说明为准。

## 十、验收判断

- R1–R13 全部已修复并有可复现的正确行为回归；原缺陷诊断全部不再成立。
- 后端 229、前端 110 测试通过；静态检查、迁移往返、真实进程、nginx SSE、四视口 UI 均通过。
- **可以交接阶段 04**，前提是接受上节“未执行”项与遗留风险；本内核已按 ADR-0004 满足 fence/暂停/取消/预算语义，可作为阶段 04 的依赖。
- 不进入阶段 04 实现；不推送远程、不发布、不动生产数据（本轮遵守）。

交接：[phase-03-fixes.md](../handoff/phase-03-fixes.md)。证据索引：[evidence/phase-03-fixes/README.md](evidence/phase-03-fixes/README.md)。
