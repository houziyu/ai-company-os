# 阶段 03 修复后独立复审

日期：2026-11-02（Asia/Shanghai）  
对应阶段：03；审查 R1–R13 修复后的工作区，未进入阶段 04。  
版本：`main`，基准提交 `0b868ca8cbe58e6ad95644d0f6d7e7db93e5701e` 上的未提交实现及修复。  
环境：Windows、仓库既有 Python 虚拟环境与 pnpm；独立 PostgreSQL 17 Alpine 容器 `acos-phase03-recheck-20261102`，仅绑定 `127.0.0.1:55441`。  
结论：**复审未通过。主要修复有效，但仍有 5 项已复现缺陷（4 项 P1、1 项 P2），当前不能支持“全部问题关闭、可直接交接阶段 04”的结论。**

## 需求与证据

| 项目 | 本轮执行 | 实际结果 | 证据 |
|---|---|---|---|
| 后端全量回归，含新增的 34 项修复回归 | backend：`python -m pytest -q` | **229 passed**，10 条依赖弃用警告，231.77 秒 | [backend-full.txt](evidence/phase-03-recheck/backend-full.txt) |
| 后端静态检查 | `ruff check .`、`ruff format --check .`、`mypy app` | 通过；73 个文件格式正确、52 个源文件类型检查通过 | [backend-static.txt](evidence/phase-03-recheck/backend-static.txt) |
| 前端完整检查 | frontend：`pnpm check` | tokens、lint、typecheck、**110 tests**、build 通过 | [frontend-check.txt](evidence/phase-03-recheck/frontend-check.txt) |
| 新增独立边界验证 | backend：`python -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-recheck/test_recheck.py -q` | **5 failed**，均为正确行为断言失败，具体原因如下 | [测试源码](evidence/phase-03-recheck/test_recheck.py)、[完整输出](evidence/phase-03-recheck/recheck.txt) |

新增测试使用真实 PostgreSQL。并发场景通过线程事件、SQL 执行钩子与可控时钟安排交错；没有跳过生产校验、替换数据库锁或修改业务代码。它们与上轮“通过表示缺陷存在”的诊断不同：**本轮新增测试通过才代表其所要求的正确行为成立**。

### V2-1 · P1：心跳在等待行锁期间过期，仍能复活租约

位置：`backend/app/services/execution_service.py:326–342`。

新增的到期条件仍使用发 SQL 之前在 Python 中取得的 `now`。如果 UPDATE 等待执行行锁，获得锁时租约可能已经过期，但 WHERE 继续比较旧时间。于是已经失效的租约仍返回续期成功，并被延长到未来。

复现：已有租约剩余 1 秒；另一事务持有执行行锁；心跳提交带旧时间的 UPDATE 并等待；可控时钟前进 2 秒后释放锁。结果 `heartbeat()` 返回 **True**，并把租约延长到起始时间后 30 秒，超过当前时刻。正确行为应为拒绝续期。

影响：原 R2 只在“进入函数时已经过期”的场景关闭，锁等待跨越到期边界仍有漏洞。数据库忙碌或事务停顿时，旧 Worker 可继续持有本应失效的执行权。

修复建议：在获得执行行锁之后取得新时间并校验/续期，或采用能够保证锁等待后重新判断实际时间的数据库方案；不要使用锁等待之前的参数时间。覆盖心跳与持锁步骤/恢复的并发，不仅测试调用前过期。

对应测试：`test_heartbeat_waiting_on_lock_cannot_resurrect_expired_lease`。

### V2-2 · P1：恢复与启动步骤的锁顺序仍不一致，可触发真实死锁

位置：`backend/app/services/execution_service.py:706–708,943–949`；后续调用预留写入位于 `BudgetService._try_hold()`。

恢复先锁 `Company FOR UPDATE` 再锁 Task；`_lock_fenced_execution()` 则从 Task 开始，没有取得公司锁。启动步骤随后插入 CALL 预留/账本时，外键检查需要公司行的 key-share 锁，会与恢复持有的公司 FOR UPDATE 冲突。

实际形成的等待环：

1. 启动步骤持有 Task/Execution 行锁。
2. 恢复持有 Company 行锁，等待同一 Task。
3. 启动步骤写预留，等待 Company 外键锁。

复现使用两个会话，在步骤门禁通过后、写预留前推进时钟使租约到期，并启动恢复。PostgreSQL 返回 **`OperationalError` / SQLSTATE `40P01`（deadlock_detected）**；本轮观测到恢复事务被选为牺牲者。

影响：至少一个事务回滚；恢复批次可能丢失本次进展，若步骤事务被回滚则可能使正常任务失败。捕获异常只能避免进程退出，不能满足“统一锁序、可靠恢复”的要求。

修复建议：对所有业务锁和隐式外键锁统一顺序；停止门禁、完成、重试、取消与恢复均需检查。当前修复交接声称共用 `company → task → execution`，实际代码未实现，文档也需同步更正。

对应测试：`test_step_start_and_recovery_do_not_deadlock`。已有 R3 回归只覆盖“步骤事务已完成全部写入、恢复随后等待”的交错，没有覆盖这里的环。

### V2-3 · P1：取消尚未执行的重试，会把旧尝试的调用证据再次计费

位置：`backend/app/services/task_service.py:613–637`。`execution_service.py::_call_evidence()` 也存在 CALL 预留只按 task_id 查询的同类作用域问题。

`_resolve_envelope_on_cancel()` 对整个任务历史查询步骤与 CALL 预留。只要上一轮有调用，就把本轮尚未使用的新规划预留当成“可能已经花费”，全额保守结算。

复现：任务以 `PERMANENT_FAILURE` 结束，公司已用 1000；手动重试，新规划预留 1000；Worker 尚未领取重试时立即取消。公司已用从 **1000 增加为 2000**，项目也再次扣费，但新尝试没有任何调用。

影响：合法取消排队重试会错误耗尽额度，产生不真实的新增用量和待核对记录。历史证据保留是正确的，但不能被用于证明另一轮新预留已发生调用。

修复建议：将证据归属限定到当前预留对应的尝试/重试批次；尚未领取的重试封套应明确视为未使用。对 queued retry cancel、claimed retry pause、第二次恢复等路径增加跨尝试断言；不能只测试首次尝试。

对应测试：`test_cancel_queued_retry_does_not_charge_previous_attempt_again`。

### V2-4 · P1：取消且用量未知的步骤仍释放任务层额度

位置：`backend/app/services/execution_service.py:437–445`。

`finish_step()` 在 `status=CANCELLED` 且 `total is None` 时调用 release。`None` 在这里表示“未获得用量”，不能证明“调用未发生”。公司/项目封套的修复没有覆盖任务层 CALL 预留。

复现：启动步骤并持有 64 Token CALL 预留，随后请求取消；以 `CANCELLED`、`prompt_tokens=None`、`completion_tokens=None`、`usage_known=False` 完成步骤。预留被写成 **RELEASED**，而正确结果应是保守结算并标记待核对。演练处理器失败分支在步骤开始后收到取消时，也会传入这种参数组合。

影响：任务层少记可能已花费的 Token，三层预算语义不一致。不能用取消状态代替无调用证据。

修复建议：只有明确可证明调用未开始才能释放；已启动但未知用量的取消步骤应保守结算。分别验证取消前未调用、已知零用量、未知用量三个不同场景。

对应测试：`test_cancelled_unknown_step_consumes_its_task_hold`。

### V2-5 · P2：发布游标迁移压缩了历史 ID 空洞，旧 Last-Event-ID 会漏新事件

位置：`backend/alembic/versions/e5b7c9d1a2f4_phase_03_publication_cursor.py:39–47`。

迁移用 `row_number()` 回填发布游标。它只保持相对顺序，不保持原数值。PostgreSQL sequence 在事务回滚后留下空洞是正常行为；旧浏览器保存的是原主键数值，不能用“相对顺序不变”证明续读兼容。

复现通过真实事务回滚制造空洞：旧已提交事件 ID 为 1、3，客户端旧游标为 3；迁移把它们改成发布游标 1、2；随后新增事件 ID=4，得到 `published_seq=3`。客户端继续 `after=3`，**新事件返回为空**。空洞多时会持续遗漏多条事件，直到发布游标追上旧值。

修复建议：采用保持旧游标数值兼容的映射（例如历史已提交行保留原 ID 数值，新发布从其最大值继续），或实施明确的游标版本/重置协议。遵守只追加迁移的约束，给出已应用本迁移环境的修复方案，不能只改写历史迁移或删除数据。更新“旧 Last-Event-ID 可继续使用”的验收说明。

对应测试：`test_migration_keeps_old_cursor_when_event_ids_have_gaps`；在专用测试库实际降级到 `c41f7b93e2a5` 再升级并检验续读。

## 原 R1–R13 的复核状态

| 原问题 | 本轮判断 |
|---|---|
| R1 已领取任务忽略暂停/取消 | 原直接复现路径已修复，新增正确行为回归通过；并发锁问题见 V2-2 |
| R2 租约到期 | 调用前已过期的路径已修复，锁等待期间过期仍失败（V2-1） |
| R3 fence 查询与写入分离 | 已加入行锁保护，原迟到写入路径有正确回归；锁序仍有死锁（V2-2） |
| R4 恢复额度不足退出 Worker | 原额度不足路径有正确回归并通过；不代表所有恢复并发路径无错误 |
| R5 取消后未知花费 | 公司/项目恢复路径已修复；任务层取消未知用量仍失败（V2-4），重试证据归属见 V2-3 |
| R6 失败步骤用量汇总 | 已改为从步骤证据汇总，原失败/未知场景回归通过 |
| R7 公司预算配置 | 共享预算依赖已注入，0/非默认值及请求顺序回归通过 |
| R8 SSE 迟提交事件 | 新发布服务覆盖反序提交；升级旧游标兼容仍失败（V2-5） |
| R9 重试部分预留 | 保存点处理已实现，原项目拒绝的回归通过 |
| R10 待核对查询 | 查询已含 SETTLED，三个投影回归通过 |
| R11 无运行执行的取消 | 状态已能到 CANCELLED；取消重试的结算仍错误（V2-3） |
| R12 查询参数 | camel/snake 参数正确行为测试通过 |
| R13 前端幂等键 | 原样原地重提的键复用测试通过；本轮未做真实浏览器断网验证 |

这些是本轮覆盖范围内的结论，不表示对每个服务所有可能输入进行了穷尽验证。

## 验收证据表达需要纠正

修复验收与交接把旧诊断“13 failed”整体当作修复证明，依据不充分。直接查看 `phase-03-fixes/review-diagnostics-after-fix.txt`：

- 原 fencing 诊断因 `_assert_fence` 被删除抛出 `AttributeError`，未进入原交错验证。
- 原 SSE 诊断因 `_fetch_events` 新增参数抛出 `TypeError`，未完成补读断言。

因此这两项旧脚本失败只证明测试与内部接口不兼容，不能证明业务正确。新建的 34 项正确行为回归是有效证据，但覆盖仍有限，本轮 5 项反例说明“全部关闭”的结论过强。原报告保留，本次通过新报告纠正，不篡改历史日志。

## UI 检查

本轮无 UI 变更。完成前端源代码审查与完整 110 项测试、类型检查和构建；未启动真实浏览器，没有本轮截图或 200% 缩放结果，不以历史截图替代本轮验证。

## 数据与安全

- 仅使用新建独立测试容器；测试数据库由原 fixture 随机创建和清理，未触碰既有容器、真实库、`.env` 或生产数据。
- 迁移反例只在该例随机测试库执行；未改动已有迁移。
- 并发死锁由 PostgreSQL 自身检出；时钟与事件钩子只控制执行顺序，不改变业务判定或模拟 SQL 结果。
- 本轮只新增报告、交接、测试证据，不修改业务代码，不推送、不部署。

## 模型验证

仅使用确定性 DEMO 处理器与服务调用，真实供应商调用 0 次。没有新增真实或模拟供应商连通性验证；既有未验证项保持原状态。

## 未通过与未执行

未通过：V2-1–V2-5，均有本轮正确行为断言与失败证据。  
未执行：本轮真实 Worker 强杀、完整 Compose/nginx SSE 故障链、真实浏览器断网、截图/200% 缩放、真实供应商。对上述项目不沿用他人报告宣称本轮已验证。

## 验收判断

修复有实质进展，现有全量测试确实通过；仍需关闭本轮 5 项缺陷并增加对应正确行为回归，再复核阶段 03 的退出条件。**本轮不批准“阶段 03 全部修复完成”的验收结论。**

交接：[phase-03-recheck.md](../handoff/phase-03-recheck.md)。
