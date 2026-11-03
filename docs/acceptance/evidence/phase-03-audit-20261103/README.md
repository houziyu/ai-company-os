# 2026-11-03 阶段 03 审查证据

- [审查报告](../../phase-03-audit-20261103.md)
- [现有检查摘要](check-summary.txt)：工具执行输出的摘要，非全量原始终端日志。
- [后端反例源码](test_audit.py)：8 个正确行为断言；当前应失败，修复后应通过。
- [A1–A7 输出](backend-counterexamples.txt)：当时源码含 7 项，全部到达业务断言并失败。
- [A9 历史账本输出](ledger-counterexample.txt)：追加第 8 项后单独执行，1 failed、7 deselected。
- [A8 组件测试](ui-audit.spec.ts)、[配置](vitest.audit.config.ts)、[输出](frontend-counterexample.txt)：1 个正确行为断言失败。模拟网络边界，非真实浏览器断网。
- `test-development-output.txt`：测试开发初稿；暂停测试因同一登录会话嵌套请求而等待认证锁，后取消测试查询并调整为独立治理事务。此文件**不是验收证据**；有效结果以上述最终输出为准。

## 重跑

只使用隔离 PostgreSQL。原 fixture 创建并删除随机测试库；不要指向真实业务数据库。

```powershell
# 若复用本轮停止的隔离容器：docker start acos-phase03-audit-20261103
# 在 backend 目录设置 TEST_ADMIN_DATABASE_URL 为该隔离实例的 /postgres URL。
./.venv/Scripts/python.exe -m pytest tests/test_tasks.py tests/test_phase03_review_fixes.py tests/test_phase03_recheck_fixes.py -q
./.venv/Scripts/python.exe -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-audit-20261103/test_audit.py -q --tb=short
./.venv/Scripts/ruff.exe check .
./.venv/Scripts/ruff.exe format --check .
./.venv/Scripts/mypy.exe app

# frontend 目录
pnpm check
pnpm exec vitest run --config ../docs/acceptance/evidence/phase-03-audit-20261103/vitest.audit.config.ts
```

容器测试账号 `acos`，本轮专用本机测试口令 `audit-local-only`，不是生产秘密。端口 55449，PostgreSQL 17.11。所有供应商网络均通过 MockTransport，测试凭据只是假数据。
