# 阶段 03 修复后独立复审交接

日期：2026-11-02（Asia/Shanghai）。[完整复审报告](../acceptance/phase-03-recheck.md)。

## 本阶段范围

已完成：按原 R1–R13 核对修复、独立运行后端全量与前端完整检查、补充并发/跨尝试预算/迁移游标验证。确认 5 项缺陷：V2-1–V2-4 为 P1，V2-5 为 P2。

未完成：上述 5 项修复和修复后验收。明确不进入阶段 04，不修改业务代码，不提交、推送或部署。

## 工程入口

- V2-1：`backend/app/services/execution_service.py::heartbeat`，等待锁后必须重新判定到期。
- V2-2：`_lock_fenced_execution` 与 `recover_expired_leases`，统一显式锁与外键隐式锁顺序。
- V2-3：`task_service.py::_resolve_envelope_on_cancel` 与 `execution_service.py::_call_evidence`，调用证据归属应精确到当前尝试/规划预留批次。
- V2-4：`execution_service.py::finish_step`，未知用量取消不能释放调用预留。
- V2-5：发布游标迁移与兼容处理，历史 ID 空洞不能被压缩后仍直接接收旧游标。
- 测试：`docs/acceptance/evidence/phase-03-recheck/test_recheck.py`，正确行为断言，本轮 **5 failed**。修复后应通过，不得改成确认缺陷存在的断言。

运行（backend 目录）：

```powershell
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://<test-user>:<test-password>@127.0.0.1:<isolated-port>/postgres'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-recheck/test_recheck.py -q
./.venv/Scripts/python.exe -m pytest -q
```

前端：`pnpm check`。静态：`ruff check .`、`ruff format --check .`、`mypy app`。

## 变更

只新增本轮验收报告、本文、独立正确行为测试和运行输出；无业务、API、状态机、迁移或设计 token 修改。历史审查和修复证据保持原样。

## 关键决定

“旧缺陷诊断全部失败”不能代替正确行为验证，尤其 `_assert_fence` 删除与 `_fetch_events` 参数变化造成的运行错误不是修复证明。修复报告中的相关验收表述需更正。

保留新发布游标机制，但修复升级兼容。已应用迁移须通过追加方案处理，不改写历史迁移、不删除用户数据。

## 验收

- 后端 229 passed、前端 110 passed；静态检查通过，均为本轮独立实测。
- 新增 5 个正确行为测试均失败，详细反例与 SQLSTATE 40P01 见报告和日志。
- 本轮没有真实浏览器、nginx 故障链、Worker 强杀或真实模型验证。

## 下一阶段接入点

先修复 V2-1–V2-5，再重跑原回归与这些新增用例。测试需包括等待锁跨越租约到期、步骤尚未写预留时与恢复交错、取消尚未执行的重试、未知用量取消、带历史 ID 空洞的迁移续读。

当前不建议直接将阶段 03 作为已完全验收的真实执行内核交给阶段 04。
