# 阶段 02 第二版复审证据

日期：2026-10-30。报告：[phase-02-recheck.md](../../phase-02-recheck.md)。

| 文件 | 内容 |
|---|---|
| `test_recheck_probes.py` | 4 项后端正确行为探针，当前代码均失败 |
| `backend-probes.txt` | PostgreSQL 3 项失败：迟到结果、迁移、非法 Unicode；`RECHECK` 行为观测摘要 |
| `timeout-probe.txt` | 真实回环 HTTP 响应头滴流，总超时断言失败 |
| `frontend-probe.spec.ts` | 刷新失败后的部分保存恢复探针 |
| `frontend-probe.txt` | 前端 1 项失败：版本 `[1,1]`，服务端版本 2 |
| `backend-baseline.txt` | 本轮现有后端全量回归原始输出 |
| `checks-summary.json` | 本轮静态/前端现有检查的实测摘要，不是原始控制台日志 |
| `source-sha256.json` | 当前应用源码与迁移的 SHA-256，不含秘密文件 |

## 复现

后端从 `backend` 执行，先确认是专用隔离 PostgreSQL；禁止把管理连接串指向业务库。

```powershell
$env:TEST_ADMIN_DATABASE_URL='<隔离 PostgreSQL 管理连接串，127.0.0.1:55434>'
$env:PYTHONPATH='.'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -m pytest ../docs/acceptance/evidence/phase-02-recheck/test_recheck_probes.py -q -s --tb=short
./.venv/Scripts/python.exe -m pytest -q
```

迁移测试只降级/升级其 fixture 新建的随机克隆库。测试运行用量均为模拟，没有外部供应商调用。当前期望失败用于证明缺陷，不代表测试基础设施故障。

前端探针引用既有 `helpers.ts`，需复制到 `frontend/src/__tests__/second-review-probe.spec.ts`，从 `frontend` 执行 `pnpm exec vitest run src/__tests__/second-review-probe.spec.ts`。运行后只移除该临时副本，保留本目录证据；不要覆盖既有正式测试。前端原有回归在放入临时探针之前执行，为 91 passed。

原始 `phase-02-review`、`phase-02-fixes` 证据均未改动。本轮没有新增截图，也没有把组件测试称为浏览器验收。
