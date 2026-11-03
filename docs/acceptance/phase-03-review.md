# 阶段 03 独立代码审查

日期：2026-11-02（Asia/Shanghai）  
对应阶段：03 持久任务调度与预算  
代码版本：`main`，基准提交 `0b868ca8cbe58e6ad95644d0f6d7e7db93e5701e`，审查对象为其上的阶段 03 未提交改动（包含 untracked 文件）。  
环境：Windows，仓库既有 Python 虚拟环境、pnpm 与锁定依赖；独立 PostgreSQL 17 Alpine 容器，端口 55439。  
本报告结论：**失败；当前阶段 03 不满足退出条件，建议先修复再进入依赖这些内核能力的阶段 04。**

发现 **13 项问题（7 项 P1、6 项 P2）**。前 12 项由真实 PostgreSQL 上的 13 个诊断场景复现；第 13 项为前端代码路径审查。未修改业务代码、已有迁移和原验收结论；本报告是后续独立审查，不覆盖历史证据。

## 需求与证据

| 检查 | 实际方法 | 实际结果 | 证据 |
|---|---|---|---|
| 阶段 03 现有后端回归 | `python -m pytest tests/test_tasks.py -q` | 25 passed | [backend-existing.txt](evidence/phase-03-review/backend-existing.txt) |
| 新增边界诊断 | `python -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-review/test_review_repros.py -q -s`（backend 目录） | 13 个异常场景全部复现，见下文 | [诊断源码](evidence/phase-03-review/test_review_repros.py)、[输出](evidence/phase-03-review/repros.txt) |
| 后端静态检查 | `python -m ruff check .`；`python -m ruff format --check .`；`python -m mypy app` | 全通过；70 个文件格式正确、51 个源文件类型检查通过 | 本次终端实测 |
| 前端完整检查 | `pnpm check` | tokens、lint、typecheck、103 tests、build 通过 | [frontend-check.txt](evidence/phase-03-review/frontend-check.txt) |
| 前端阶段 03 定向回归 | `pnpm test -- src/__tests__/phase03-views.spec.ts` | 10 passed | 本次终端实测，亦包含在完整检查中 |

**诊断脚本的 passed 表示“确认当前缺陷存在”，不表示产品通过验收。** 修复时应把这些观察断言改为正确行为断言并纳入正常回归，不能继续用缺陷观察断言作为通过标准。

### R1 · P1：已领取任务绕过公司暂停和取消检查

位置：`backend/app/services/execution_service.py:282–290`；`backend/app/workers/demo_processor.py:159–174`。

`start_step()` 只检查 fencing，随后直接创建步骤和预留，不检查公司暂停或任务取消；处理器在每步开始前也不补检。`SUCCESS` 路径甚至从不读取取消标记。

**实测**：领取任务后、尚未启动任何步骤时，通过 HTTP 暂停公司或取消任务，再让真实 `WorkerRuntime.process_claim()` 执行，两种情况都启动完整两步并最终 `SUCCEEDED`。这与“暂停阻止新调用”“到安全检查点停止”相违背。当前仅演练，不代表已经发生真实供应商花费，但该内核不能按交接声明直接用于真实调用。

修复：把公司状态、取消请求、有效租约和预算检查纳入每一步启动的服务端事务；停止信号在成功/失败/慢步骤各路径使用同一入口。补上“已领取但未开始”“步骤之间暂停/取消”的回归。

证据：`test_running_task_ignores_stop_gate[pause/cancel]`。

### R2 · P1：过期租约仍可写入并被心跳重新延长

位置：`backend/app/services/execution_service.py:250–259,702–724`。

心跳、步骤 fencing 和完成检查都只看 token/status，没有检查 `lease_expires_at`。恢复扫描尚未处理过期行时，失效 Worker 仍被当成有效持有者。

**实测**：将租约设为过去 10 秒，`start_step()` 仍成功；接着 `heartbeat()` 返回 true 并把过期租约重新延长。进程长时间挂起再恢复或数据库故障后恢复时可触发。

修复：所有租约敏感写入和续约必须在数据库判定租约尚未到期；失效后拒绝复活，并让 Worker 停止后续步骤。不要只依赖恢复扫描及时运行。

证据：`test_expired_lease_can_write_and_renew`。

### R3 · P1：步骤 fencing 是普通查询，检查与写入之间存在竞争窗口

位置：`backend/app/services/execution_service.py:714–724`，调用方 `start_step` / `finish_step`。

`_assert_fence()` 的 SELECT 没有锁定执行行，也没有把条件绑定在后续步骤写入上。恢复流程可以在检查通过后提交 `INTERRUPTED`，旧 Worker 随后仍然写步骤与预算。这不同于 R2：即使补上时间条件，检查和写入分离仍会出错。

**实测**：在正常 fence 查询返回后插入另一个会话的租约恢复并提交，旧调用仍成功创建 `RUNNING` 步骤及调用预留，所属执行已经 `INTERRUPTED`。诊断只控制两操作的交错顺序，没有放宽生产验证。

修复：通过统一锁序下的执行行锁或等效条件写入，使 fence 判定与步骤/账本变更处于同一受保护事务。覆盖恢复与开始/完成步骤同时发生的情况。

证据：`test_fence_check_does_not_protect_following_write`。

### R4 · P1：恢复时没有重试额度会使 Worker 异常退出

位置：`backend/app/services/execution_service.py:630–656`；`backend/app/workers/task_worker.py:recover`。

恢复路径先保守结算旧预留，随后无保护地申请新预留。额度不足抛出 `BudgetInsufficientError`，而 `WorkerRuntime.recover()` 只捕获 `SQLAlchemyError`。异常会越过启动/周期恢复循环，整个事务回滚。

**实测**：项目额度 1000、任务额度 1000、允许一次自动重试；任务租约到期后调用恢复，异常逃出 `WorkerRuntime.recover()`，数据库中任务和旧执行仍为 `RUNNING`。相同数据下再次启动还会失败，并阻碍该 Worker 消费其他排队任务。

修复：旧执行的中断与保守结算必须可靠落库；重试额度不足应保存 `BLOCKED`/待人工处理结果，而非撤销恢复。新预留失败不应导致整个批次或进程退出。

证据：`test_recovery_without_retry_budget_escapes_worker`。

### R5 · P1：取消后 Worker 丢失，会释放可能已经花费的公司/项目额度

位置：`backend/app/services/execution_service.py:612–618`。

恢复流程已经将未完成调用判定为用量未知，却仅因存在取消请求而调用 `release_planning_holds()`。取消请求无法证明调用未花费。

**实测**：启动并预留一个 200 Token 步骤，请求取消后使租约过期。任务账户保守消耗 200，但公司与项目的已用和预留全部归零。这会使上层预算被重新用于其他任务，绕过总额度约束。

修复：依据实际调用与用量证据结算；只有证明从未启动调用的预留才可以释放。取消标记不能替代用量证据。

证据：`test_cancelled_recovery_releases_unknown_company_cost`。

### R6 · P1：失败步骤的未知用量被汇总成“已知”

位置：`backend/app/workers/demo_processor.py:220–233`。

临时/永久失败分支直接返回前面成功步骤累计的 `usage_known` 和 Token 数，没有把当前失败步骤的 `spec.known=False` 纳入执行汇总。

**实测**：`PERMANENT_FAILURE` 第一步已知消耗 200，第二步用量未知且任务层保守计入 64。任务层已用为 264，公司/项目仅记 200；执行却标记 `usage_known=True`、无需核对。`TRANSIENT_FAILURE` 同样经过此逻辑。

修复：执行汇总必须纳入所有已启动步骤；任何一笔未知用量不能被已知部分掩盖。上层结算须保守保留未知部分，不能释放成可用余额。

证据：`test_failed_step_unknown_usage_becomes_known_execution`。

### R7 · P1：首次创建任务可以忽略配置的公司预算上限

位置：`backend/app/services/task_service.py:263–266`。

`TaskService` 自行构造默认 `BudgetService()`，而应用配置只注入 `app.state.budget_service` 和模型服务。新公司尚无预算账户且先创建任务时，会使用硬编码的 1,000,000，而非 `COMPANY_DEFAULT_BUDGET_TOKENS`。访问预算页面与提交任务的先后顺序因此改变最终额度。

**实测**：应用设置与读预算服务的默认额度都为 0，首次任务仍返回 201，落库公司账户额度为 1,000,000。

修复：任务服务复用配置好的预算依赖，或在公司创建事务中统一建立正确额度的账户。覆盖非默认值、0，以及不同请求先后顺序。

证据：`test_task_creation_ignores_configured_zero_budget`。

### R8 · P2：SSE 用自增 ID 当提交顺序，可能永久漏事件

位置：`backend/app/api/v1/tasks.py:822–831,865–868`。

自增 ID 分配顺序不等于事务提交顺序。两个 Worker 领取不同任务时，较小事件 ID 所在事务可以较晚提交；流先看到较大 ID 并推进 cursor，之后永远不再查询较小 ID。

**实测**：两个真实数据库会话分别通过 `claim_next()` 产生事件 4 和 5；先提交事件 5，查询流返回 5；再提交 4，从 cursor=5 查询为空。事件 4 已持久化却不再投递，重连也补不回来。

修复：使用具有可证明提交顺序的发布游标/串行发布机制，或支持迟提交事件的可靠补读策略；不能仅凭 sequence 单调递增宣称无遗漏。前端重连还应校准权威查询。

证据：`test_concurrent_commits_skip_sse_event`。

### R9 · P2：自动重试的两层预留不是原子操作，会留下公司额度占用

位置：`backend/app/services/execution_service.py:455–484`。

公司预留成功、项目预留失败时，被广泛 `except Exception` 吞掉并转为 `BLOCKED`，但没有撤销已经写入的公司预留。任务不再执行，该额度却继续占用。

**实测**：项目额度 1000、任务额度 1000，临时失败后项目剩余额度不足重试；任务变为 `BLOCKED`，仍留一笔公司级 1000 Token 的 `RESERVED`，没有对应项目预留。

修复：把新一轮双层预留放入同一保存点，失败时整体回滚，同时保留上一轮执行结算；只捕获预期业务错误。对“公司成功、项目失败”的边界加断言。

证据：`test_auto_retry_leaks_partial_company_hold`。

### R10 · P2：待核对查询过滤错状态，实际未知用量显示为零项

位置：`backend/app/api/v1/tasks.py:647–648,703–708`；`backend/app/services/budget_service.py:183–191`。

未知用量结算后记录是 `SETTLED + needs_reconciliation=true`，但概览总数、账户计数和明细接口都限定 `status=RESERVED`，排除了需要核对的结算。

**实测**：执行 `UNKNOWN_USAGE` 后数据库存在 4 条待核对预留，预算 API 的总数与公司计数仍为 0，待核对明细返回 `[]`。

修复：按核对标记查询所有未核对记录，区分已保守结算与尚未结束的调用；同步三个读出口。未知用量测试应覆盖 API 投影而不只检查账本。

证据：`test_unknown_reservations_invisible_in_reconciliation_api`。

### R11 · P2：取消中断/阻塞任务后永远无法到达取消终态

位置：`backend/app/services/task_service.py:506–539`。

只有 `QUEUED` 直接进入 `CANCELLED`；其他非终态全部被假设有 Worker 负责收尾。`INTERRUPTED`/`BLOCKED` 已无运行中的执行，只留下取消标记，随后重试又被该标记拒绝。

**实测**：无自动重试的过期任务进入 `INTERRUPTED`，取消接口返回 200，但状态不变；重试返回 409。没有 Worker 会领取它来完成取消。

修复：无有效运行执行的可取消状态直接落取消终态，并按实际用量处理剩余预留；有运行执行才进入协作式取消。必须按状态机而非“非 QUEUED 就有人执行”判断。

证据：`test_cancelling_interrupted_task_never_completes`。

### R12 · P2：查询参数命名不一致，项目筛选被静默忽略

位置：`backend/app/api/v1/tasks.py:343,628,839`；`frontend/src/api/endpoints.ts`。

前端发送 `projectId` / `ledgerLimit`，后端查询参数只接受 `project_id` / `ledger_limit`，没有 alias。响应 DTO 的 camelCase 配置不适用于这些独立 Query 参数。

**实测**：两个项目各有一个任务，`GET /tasks?projectId=1` 返回两个项目的任务；使用 `project_id=1` 才正确过滤。`/budgets?ledgerLimit=1` 返回 4 条，而 `ledger_limit=1` 返回 1 条。SSE 的 `projectId` 参数也存在相同声明问题。

修复：统一公开参数名称并增加真实前端请求参数的 API 契约测试。否则看板项目切换会混入其他项目任务，账本条数设置无效。

证据：`test_frontend_query_names_are_ignored`。

### R13 · P2：前端失败重提会生成新幂等键，可能重复创建任务与预留

位置：`frontend/src/views/ProjectsView.vue:226–245`。

幂等键在每次 `submitTask()` 内重新生成。若第一次服务端已经提交任务而响应丢失，用户保留表单再次提交会得到一个新键；服务端因此合法创建第二个任务和第二份预留。当前前端测试只验证键存在，没有验证失败后重用。

验证方式：代码路径审查。失败路径保留草稿，下一次按钮点击无条件执行 `crypto.randomUUID()`；共享 API 层也没有保存该次提交的键。**本轮未执行浏览器断网复现**。

修复：把幂等键绑定到一次逻辑提交及其固定请求体，未知结果下重试沿用原键；确认成功或用户明确开始另一份请求后再换键。增加“后端提交成功、客户端丢响应、原样重提”测试。

## UI 检查

本轮没有 UI 实现变更；完成前端静态审查、103 项组件/逻辑测试及构建。没有重新启动真实浏览器、截图或 200% 缩放验收；不以历史截图替代本轮 UI 验证。R12 通过真实 API 请求复现；R13 为静态代码证据。

## 数据与安全

- 独立创建 `acos-phase03-review-20261102` PostgreSQL 容器，仅映射 `127.0.0.1:55439`；未使用既有 Compose 栈或用户数据库。
- 测试复用仓库 fixture，每例建立随机克隆数据库，执行结束由 fixture 删除其自身测试库。迁移升到当前 head；没有执行历史数据降级/恢复演练。
- 未读取或修改 `.env`，没有真实模型密钥，没有对外模型调用。
- R1–R7 阻断暂停、租约、预算和恢复门禁；不能因基础测试全绿忽略。
- 本轮仅新增审查报告、交接与诊断证据，不修复业务代码，不提交或推送。

## 模型验证

本轮仅调用确定性 DEMO 处理器；真实供应商调用 0 次。没有新增协议模拟连通性验证；原阶段 02 的真实供应商与强杀恢复缺口保持未验证。

## 未通过与未执行

未通过：R1–R13，详见各项触发条件、影响和修复建议。  
未执行：后端全量 195 项、迁移降级/升级往返、OS/容器 Worker 强杀、完整 Compose/nginx SSE、真实浏览器、200% 缩放、真实供应商。此次数据库迁移创建与阶段 03 定向测试通过不能替代这些检查。

## 验收判断

阶段 03 原有 25 项后端回归和前端全量 103 项通过，但新增诊断确认关键退出条件不成立。**不能确认阶段 03 已通过，也不建议直接复用该内核进入真实调用阶段。** 优先关闭 R1–R7，再修复事件补读、额度遗留、读模型和前端幂等问题。修复后运行相应新增回归及阶段 03 原有验收。

交接：[phase-03-review.md](../handoff/phase-03-review.md)。
