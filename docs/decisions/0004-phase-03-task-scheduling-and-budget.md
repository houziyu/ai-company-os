# ADR-0004：阶段 03 持久任务队列、租约与 Token 预算

日期：2026-11-02
状态：采用（阶段 03 实施决策，随验收记录留档）
来源：本阶段工程决定；「任务与执行分离」「未知用量不得记 0」「真实模式不得静默降级」来自产品基线
替代或受影响 ADR：延展 ADR-0003 的探测与用量语义，不改其结论

## 背景

阶段 03 要让任务真正排队、被 Worker 领取、可中断可恢复，并让每一次付费调用都受额度约束。有六处若按直觉实现，后期很难纠正：

1. **队列放哪里**：内存队列或外部队列组件会在进程重启后丢任务；本项目已确定 PostgreSQL 为唯一事实来源。
2. **两个 Worker 抢同一任务**：应用层「先查后改」在多进程下必然双领。
3. **Worker 被强杀**：进程消失后，它的结果可能晚到；晚到的写回会覆盖新状态。
4. **预算并发**：「先读余额再扣」在两个请求同时到达时会超支。
5. **未知用量**：供应商没返回 Token 时，若当成 0 结算，公司账本会系统性少记。
6. **取消语义**：浏览器断开、进程被杀、用户点取消是三件不同的事，混为一谈会伪造「已取消」。

## 决定

**队列就是任务表本身。** 领取用 `SELECT ... FOR UPDATE SKIP LOCKED` 加条件 `UPDATE ... WHERE status='QUEUED'`；数据库裁决胜者，输家影响 0 行。没有第二个组件可以失去同步，重启不丢队列。

**fencing token 单调递增，且随每次领取在 SQL 中自增**（`fencing_counter = fencing_counter + 1 ... RETURNING`）。每次执行写入时携带领取时的 token，条件更新 `WHERE fencing_token = :token`；不匹配即 `LeaseLostError`，调用方必须放弃而不是重试写入。这让「旧租约迟到结果」在数据库层面变成无操作，而不是靠时序运气。

**租约与心跳。** `lease_expires_at` 由心跳延长，心跳同样带 token 条件；延长失败即「我知道我失去了这次尝试」。租约过期由恢复流程处理：标记 `INTERRUPTED` 且 `interrupted_unknown_side_effects = true`。

**只有声明幂等的处理器才自动重放。** 恢复时若处理器声明幂等（演练处理器只写自己的步骤行）且重试预算未用完，才重新排队；否则停在 `INTERRUPTED` 等人工重试。未知副作用不盲目重放。

**预算用三层账户与两类预留。** 公司/项目/任务各一个账户，锁顺序固定为公司→项目→任务。任务创建时在公司与项目各持一个 `PLANNING` 预留（整包额度）；每个步骤开始时在任务账户持一个 `CALL` 预留（该步估算）。扣减用条件 `UPDATE ... WHERE limit - consumed - reserved >= amount`，账户余额的非负由检查约束兜底。

**未知用量按「消耗整笔预留 + 标记待核对」结算。** `usage_tokens` 保持 `NULL`，`needs_reconciliation = true`，`delta_consumed` 取预留额。释放会让账面少记一次可能已发生的调用，因此不允许。账本检查约束显式允许「结算且用量未知但已标记待核对」，并禁止「结算且既无用量又未标记」。

**结算幂等由唯一索引保证**：`(account_id, entry_kind, reservation_id)` 唯一；重复结算返回 `None` 而不是第二次记账。

**未结算的预留唯一性只对 open 行生效**（部分唯一索引 `WHERE status='RESERVED'`）。重试会合法地再开一个规划预留，全局唯一会把合法重试当成冲突。

**创建任务、预留与事件同事务。** 任务行、公司/项目预留、任务账户、时间线与领域事件一次提交；项目额度不足时整个事务回滚，不会留下孤儿预留——这一点在验收里以「失败后账户与预留均为空」断言。

**幂等键带内容摘要。** `(company_id, idempotency_key)` 唯一，另存请求体摘要：同键同摘要返回既有任务（200），同键异摘要 409 `IDEMPOTENCY_CONFLICT`。

**取消是协作式的，且状态由服务端决定。** 排队中的任务立即 `CANCELLED` 并释放预留；运行中的任务只记录 `cancel_requested_at`，由 Worker 在安全检查点停止并写 `CANCELLED`。响应文案明确说「会在安全检查点停止」，不假装立即停止。浏览器断开从不被当作取消。

**真实运行模式被拒绝，不降级。** `runMode=REAL` 返回 409 `RUN_MODE_UNAVAILABLE`；不创建项目、不改成 DEMO。演练处理器在每条任务、执行与页面上都有「演练运行（确定性处理器，非真实模型输出）」标签。

**阶段 02 的探测纳入公司预算。** 探测在提交 `PENDING` 意图的同一短事务内持一笔公司 `PROBE` 预留，结束后按上报用量结算；进程中途死亡则预留保持 `RESERVED`，即「可能已付费」。公司暂停或额度不足时探测在本地被拒（`call_count = 0`），不发请求。**公司尚未创建时不占预算**：阶段 02 允许在建公司前配置并测试模型，此时没有任何额度可占用；探测照常记录用量，响应如实说明未受预算约束。

**SSE 用单调事件 ID，断线按 ID 续读。** `domain_events` 自增主键即游标；客户端发送 `Last-Event-ID`（或 `?after=`），服务端严格回放其后事件。**非 2xx 响应是 EventSource 的致命错误，浏览器不会自动重试**，因此前端自行按指数退避重连并从最后 ID 续读；这一点有回归测试。

**降级会把暂停状态复位。** `companies.status` 增加 `PAUSED`；降级回阶段 02 前先把 `PAUSED` 复位为 `ACTIVE`，否则旧约束会拒绝自身数据。降级删除阶段 03 全部表，只在无任务数据的库上安全。

## 备选与取舍

- *外部消息队列（Redis/RabbitMQ）*：会引入第二处事实来源和新的部署组件；在单公司、低并发的当前规模下，PostgreSQL 队列足够，且事务与业务写入天然一致。
- *应用层分布式锁*：需要额外的锁服务与超时处理，且锁过期与结果迟到同样需要 fencing，等于多做一层。
- *把预算存成一行 JSON*：无法用检查约束保证非负，也无法用条件更新原子扣减。
- *未知用量记 0*：会让预算看起来比实际更宽裕，是隐蔽的财务错误。
- *取消时直接杀掉执行*：真实外部调用无法撤回，宣称「已取消」会伪造事实；协作式停止 + 记录停止点是唯一诚实的做法。
- *失败自动无限重试*：放大费用且掩盖问题；限制次数并要求处理器声明幂等。

## 影响与迁移

- 新增 10 张表（`projects`、`tasks`、`task_executions`、`task_execution_steps`、`task_events`、`domain_events`、`task_messages`、`budget_accounts`、`budget_reservations`、`budget_ledger_entries`）与迁移 `c41f7b93e2a5`，head 由 `8c24a71d9e03` 前移。已执行迁移不改写。
- `companies` 增加 `paused_at`、`pause_reason` 两列，`status` 约束由 `ACTIVE` 扩为 `ACTIVE/PAUSED`，并新增 `pause_pairing` 检查约束。
- 迁移为每个既有公司补建一个 `COMPANY` 预算账户，额度 0：不虚构「无限额度」，也不静默放开花费；运营需显式设置上限。
- Compose 的 `worker` 命令由心跳循环改为 `app.workers.task_worker`；新增 `WORKER_LEASE_SECONDS`、`WORKER_POLL_INTERVAL_SECONDS`、`DEMO_SLOW_STEP_SECONDS` 三个配置项。隔离验收栈 `acos-phase03-test` 再起一个 `worker2`，用于双 Worker 竞争与容器强杀证据。
- 新增配置 `COMPANY_DEFAULT_BUDGET_TOKENS`（默认 1,000,000）与 `PROBE_RESERVATION_TOKENS`（默认 512）。前者是可见、可改的真实额度，不是「无限」。
- 未新增 Python/前端依赖。

## 验证与回退

见[阶段 03 验收](../acceptance/phase-03.md)：

- 真实进程：uvicorn + 两个独立 worker 进程，操作系统级强杀（Windows `taskkill /F` / POSIX `SIGKILL`），中断→重新排队→完成。
- 容器级：两个 worker 容器同镜像，`docker kill` 持租约容器，存活容器恢复；整栈停机再启动后排队任务被执行、运行中尝试被标记中断并重放。
- 数据库不变量：账户余额非负、无遗留 open 预留、无未标记的未知用量。
- 迁移：`8c24a71d9e03 → c41f7b93e2a5` 正反向与再升级，阶段 02 的探测/用量/公司数据逐项比对不变。
