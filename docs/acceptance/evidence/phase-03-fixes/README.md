# 阶段 03 修复证据索引（R1–R13）

日期：2026-11-02。关联[修复验收](../../phase-03-fixes.md)与[交接](../../../handoff/phase-03-fixes.md)。

所有文件为本轮实际执行产物。环境：独立 PostgreSQL 17 容器 `acos-phase03-fix-20261102`（`127.0.0.1:55440`），未使用开发/生产数据。

## 结论文件

| 文件 | 内容 | 结果 |
|---|---|---|
| `baseline-defects-reproduced.txt` | **修复前**运行原审查诊断：13 个缺陷全部复现 | 13 passed（= 缺陷存在） |
| `review-diagnostics-after-fix.txt` | **修复后**运行同一诊断 | 13 failed（= 缺陷不再存在） |
| `regression-suite.txt` | `tests/test_phase03_review_fixes.py` 逐项 | 34 passed |
| `backend-full.txt` | `pytest -q` 全量 | 229 passed |
| `backend-static.txt` | ruff / format / mypy | 全部通过 |
| `frontend-check.txt` | `pnpm check`（tokens/lint/typecheck/test/build） | 110 passed |
| `migration-roundtrip.txt` | `scripts/verify_phase03_fix_migration.py` | PASS |
| `runtime/runtime-results.json` + `runtime/process-logs/` | 真实 uvicorn + 2 worker 进程，含 OS 强杀 | 10/10 |
| `nginx-sse/results.json` + `nginx-sse-run.txt` + `nginx-sse/nginx.log` | 真实 `deploy/nginx/default.conf` 代理后的 SSE 断线补读 | 3/3 |
| `nginx-sse/nginx-mount/` | 渲染后的 nginx 配置（仅替换 upstream 主机端口） | 证据 |
| `ui/ui-results.json` + `ui/*.png` + `ui-run.txt` | 四视口 UI 验收，40 张截图 | 0 页面异常 |
| `ui-api.log` / `ui-worker.log` | UI 验收期间真实 API 与 worker 日志 | 证据 |

## 逐项对应

| 发现 | 正确行为回归 | 其它证据 |
|---|---|---|
| R1 暂停/取消被绕过 | `test_r1_claimed_but_not_started_task_honours_stop_command`、`test_r1_pause_between_steps_stops_before_the_next_call` | runtime `stop-gate-after-claim` |
| R2 过期租约可写/可续 | `test_r2_expired_lease_cannot_write_or_be_renewed`、`test_r2_late_write_after_recovery_is_refused` | — |
| R3 fencing 竞争窗口 | `test_r3_recovery_waits_for_an_in_flight_step_transaction`、`test_r3_stale_worker_cannot_write_after_recovery` | runtime `worker-killed`/`idle-requeue` |
| R4 恢复额度不足崩溃 | `test_r4_recovery_without_retry_budget_blocks_without_crashing`、`test_r4_recovery_without_any_call_is_not_charged` | runtime `idle-requeue` |
| R5 取消后释放未知花费 | `test_r5_cancel_after_call_started_keeps_company_cost_conservative` | — |
| R6 失败步骤用量汇总 | `test_r6_failed_step_unknown_usage_makes_the_attempt_unknown`、`test_r6_mixed_known_and_unknown_steps_keep_the_unknown_part` | — |
| R7 公司默认额度被忽略 | `test_r7_first_task_uses_the_configured_company_default[0|7777]`、两个顺序一致性用例 | — |
| R8 SSE 漏事件 | `test_r8_late_committed_event_is_not_skipped`、`test_r8_cursor_is_stable_for_already_committed_events`、`test_r8_http_replay_uses_the_publication_cursor` | nginx `resume-behind-nginx` |
| R9 重试预留非原子 | `test_r9_auto_retry_leaves_no_partial_company_hold`、`test_r9_manual_retry_leaves_no_partial_hold_when_project_is_short` | — |
| R10 待核对查询过滤错状态 | `test_r10_settled_unknown_usage_is_visible_in_every_projection`、`test_r10_open_unknown_hold_is_distinguished_from_settled` | `ui/budgets-*.png` |
| R11 非运行任务取消不到终态 | `test_r11_cancel_interrupted_task_reaches_cancelled`、`test_r11_cancel_blocked_task_reaches_cancelled`、`test_r11_cancel_running_task_stays_cooperative` | runtime `cancel-running-flag` |
| R12 查询参数被忽略 | `test_r12_frontend_query_names_filter_and_limit`、`test_r12_sse_accepts_the_frontend_project_parameter` | — |
| R13 前端幂等键生命周期 | `frontend/src/__tests__/phase03-idempotency.spec.ts`（7 项）、`test_r13_same_key_after_lost_response_yields_one_task_and_one_hold`、`test_r13_different_body_under_the_same_key_is_refused` | — |

## 说明

- 原审查诊断脚本位于 `../phase-03-review/test_review_repros.py`，**未修改**。它的断言是“缺陷存在”，因此修复后失败是预期且正确的；不得把它当作验收通过条件。
- `runtime/`、`nginx-sse/`、`ui/` 使用各自独立的临时数据库（`acos_runtime`、`acos_sse`、`acos_ui`），均在独立容器内，未触碰真实数据。
- 未执行项：真实模型调用、真实浏览器 200% 缩放、阶段 02 强杀恢复、R13 真实浏览器断网、完整 Compose 整链、生产库迁移演练。
- 历史 UI 截图目录 `../phase-03/ui/` 被本轮一次误覆盖，详见验收第九节。
