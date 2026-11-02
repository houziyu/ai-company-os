# 阶段 02 独立质检证据

报告：[独立质检](../../phase-02-review.md)。日期：2026-10-30。

| 文件 | 含义 |
|---|---|
| `checks-summary.json` | 实际检查结果摘要；不是原始控制台输出 |
| `baseline-first-attempt.txt` | 首轮 141 项通过结果和输出缓冲说明 |
| `backend-baseline.txt` | 后端原有测试详细复跑；包含 45 秒诊断栈打印，不等于测试失败 |
| `test_review_probes.py` | 后端独立正确性断言；当前实现下 11 项失败 |
| `backend-probes.txt` | 上述探针输出；`REVIEW` 行含结构化观测 |
| `frontend-probes.spec.ts` | 前端三个交互探针；当前实现下均失败 |
| `frontend-probes.txt` | 前端探针原始输出 |
| `source-sha256.json` | 本次应用源码/迁移文件指纹，不含 `.env` 或秘密 |

## 复现

所有调用为模拟；R6 是真实本机 HTTP 协议模拟，不是外部模型调用。

测试数据库必须是独立测试实例。可以启动一个新命名的 PostgreSQL，绑定空闲回环端口，再设置 `TEST_ADMIN_DATABASE_URL`；不要使用真实业务库。测试 fixture 只创建/移除自己命名的随机测试库。

在 `backend` 执行（连接串请使用你的独立实例）：

```powershell
$env:PYTHONPATH='.'
$env:PYTHONIOENCODING='utf-8'
$env:TEST_ADMIN_DATABASE_URL='<你的独立测试 PostgreSQL 管理连接串>'
./.venv/Scripts/python.exe -m pytest ../docs/acceptance/evidence/phase-02-review/test_review_probes.py -q -s --tb=short
```

前端探针依赖同目录的 `helpers.ts`，所以需临时复制到既有前端测试目录，在执行后移除这份临时文件（保留证据原件）。从仓库根目录执行：

```powershell
Copy-Item -LiteralPath docs/acceptance/evidence/phase-02-review/frontend-probes.spec.ts -Destination frontend/src/__tests__/phase02-review.probes.spec.ts
Push-Location frontend
pnpm exec vitest run src/__tests__/phase02-review.probes.spec.ts
Pop-Location
Remove-Item -LiteralPath frontend/src/__tests__/phase02-review.probes.spec.ts
```

探针按正确业务结果断言，目前失败是本次发现的证据；不能把它们作为“新增测试已通过”。转正式回归后应核对修复契约并保留此处修复前输出。
