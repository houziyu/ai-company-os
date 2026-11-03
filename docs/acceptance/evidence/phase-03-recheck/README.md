# 阶段 03 修复后复审证据

日期：2026-11-02。关联[复审报告](../../phase-03-recheck.md)。

- `test_recheck.py`：5 个新增正确行为测试，当前全部失败，分别证明 5 项剩余缺陷。
- `recheck.txt`：该文件最终版本整组运行结果，5 failed。
- `deadlock.txt`：锁序反例的单项输出，真实 PostgreSQL SQLSTATE 40P01。
- `backend-full.txt`：后端全量，229 passed。
- `backend-static.txt`：ruff、格式、mypy 通过。
- `frontend-check.txt`：pnpm check，110 tests 与构建通过。

仅使用独立容器 `acos-phase03-recheck-20261102` / 127.0.0.1:55441。fixture 管理随机测试库；没有触碰既有数据库。复审结束已停止该容器，保留容器供复查。本轮测试断言正确行为，失败表示缺陷尚未解决，不能反向解释为验收通过。
