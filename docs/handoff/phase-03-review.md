# 阶段 03 独立审查交接

日期：2026-11-02（Asia/Shanghai）。[完整审查](../acceptance/phase-03-review.md)。

## 本阶段范围

已完成：审查工作区阶段 03 后端、Worker、预算、SSE、前后端契约及关键前端提交路径；13 项发现，7 项 P1、6 项 P2。12 项发现有真实 PostgreSQL 的 13 个诊断场景，1 项为静态前端路径证据。

未完成：业务缺陷修复与修复后验收；这些不属于本次“审查代码”的实施范围。没有进入阶段 04。

## 工程入口

- `backend/app/services/execution_service.py`：R1–R5、R8/R9 所涉及的租约、恢复、事件及预算边界。
- `backend/app/workers/demo_processor.py`：R1、R6 的步骤停止与未知用量汇总。
- `backend/app/services/task_service.py`：R7 预算依赖、R11 非运行状态取消。
- `backend/app/api/v1/tasks.py`、`budget_service.py`：R8、R10、R12 的事件游标和读取契约。
- `frontend/src/views/ProjectsView.vue`：R13 提交键生命周期。
- 诊断：`docs/acceptance/evidence/phase-03-review/test_review_repros.py`。**当前断言确认缺陷存在，修复时必须改为正确行为断言，不能将它们当验收通过条件。**

运行诊断（backend 目录，环境变量必须指向独立测试实例）：

```powershell
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://<test-user>:<test-password>@127.0.0.1:<isolated-port>/postgres'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -m pytest -p tests.conftest ../docs/acceptance/evidence/phase-03-review/test_review_repros.py -q -s
./.venv/Scripts/python.exe -m pytest tests/test_tasks.py -q
```

## 变更

只新增审查报告、本文与证据目录。没有业务代码、API、状态机、数据表、迁移或设计 token 的修改；没有提交、推送或部署。原验收文档保留原样。本次实际检查后由前端构建重新生成的 dist 不属于源码修复。

## 关键决定

不因为原测试通过而接受阶段 03：真实诊断已证明暂停、租约、未知用量和恢复不满足原 ADR-0004 与阶段计划。此次没有引入新产品决策或变更架构。

建议修复顺序：统一调用前检查与有效租约保护；修复恢复事务、未知用量及预算依赖；修复事件提交顺序、失败预留清理和取消状态机；补齐读取投影、参数契约与前端逻辑提交幂等。

## 验收

- 现有后端阶段 03：25 passed；ruff、format、mypy 通过。
- 新诊断：13 个异常场景确认存在；不构成产品验收通过。
- 前端完整 `pnpm check`：103 tests、静态检查和构建通过。
- 未重跑全后端、浏览器、强杀、迁移往返或真实模型。详细边界见审查报告。

## 下一阶段接入点

先修复阶段 03，再交付依赖此内核的阶段 04。不得直接照原交接将 `start_step`、`finish_step`、`complete` 当成已经满足 fence/暂停/取消语义的通用入口。

后续修复任务可依据 R1–R13 逐项实施；保存历史证据，建立正确行为回归，重新出具验收结论。测试替身只能控制调度时序，不能放宽生产权限或预算校验。
