# 阶段 02 修复交接（独立质检 R1–R11）

日期：2026-10-30。验收报告：[phase-02-fixes.md](../acceptance/phase-02-fixes.md)。证据索引：[evidence/phase-02-fixes/README.md](../acceptance/evidence/phase-02-fixes/README.md)。原始质检：[phase-02-review.md](../acceptance/phase-02-review.md)（未改动）。

## 本阶段范围

已修复独立质检的 11 项发现（3 个 P1、7 个 P2、1 个 P3），全部有修复前失败与修复后通过的证据。未新增阶段 03 能力，未改动登录/会话/CSRF/历史保留策略。

不在本范围：任务调度与预算（03）、隔离执行器（04）、招聘与调岗审批（06）、员工账号。真实浏览器 200% 缩放与真实供应商连通性仍未验证（原因未变，见验收）。

## 工程入口

| 文件 | 本轮职责 |
|---|---|
| `backend/app/providers/adapter.py` | R1 元数据筛查、R4 用量维度、R5 校验与独立用量解析、R6 流式总时限与体积上限 |
| `backend/app/services/model_centre_service.py` | R2 两阶段探测、R3 指纹与失效、R5 统一结果写入、R7 版本校验、R9 备注契约 |
| `backend/app/repositories/organization_repository.py` | `create_intent` / `create_refusal` / `close_current`、per-dimension 用量汇总 |
| `backend/app/models/organization.py` | 版本、指纹、`PENDING`、用量维度列 |
| `backend/alembic/versions/3f8d2c6a91b4_phase_02_review_fixes.py` | 上述列与约束、旧数据回填 |
| `backend/app/api/v1/organization.py` | `POST /agents/{id}/model-unbind`（R11） |
| `backend/app/api/v1/model_centre.py` | 版本透传、过期证据标记、用量维度、`PENDING` 展示 |
| `backend/app/api/schemas_organization.py` | `expectedVersion` 必填（R7）、版本与指纹字段、用量维度、解绑请求 |
| `frontend/src/views/OrganizationView.vue` | R8 分步恢复与部分保存提示、R11 解绑 |
| `frontend/src/views/ModelsView.vue` | R10 标签键盘、R7 版本透传、R9 备注清空、R4/`PENDING`/过期证据展示 |
| `backend/tests/test_review_fixes.py` | R1–R11 正式回归（18 项，带 `REVIEW` 观测行） |
| `frontend/src/__tests__/review-fixes.spec.ts` | R7/R8/R10/R11 前端回归（8 项） |

检查命令：

```powershell
# 后端
cd backend
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app
$env:TEST_ADMIN_DATABASE_URL='<隔离测试实例>'
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pytest tests/test_review_fixes.py -q -s   # 带 REVIEW 观测

# 前端
cd ../frontend
pnpm check

# 隔离栈
./scripts/phase02_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_ui.cjs
node scripts/verify_phase02_zoom200.cjs --method=equiv
```

## 变更

**迁移 `3f8d2c6a91b4`（只加列/约束，不建表、不删数据）**

- `providers`: +`version`（默认 1，`CHECK >= 1`）、+`config_fingerprint`（默认 `''`）。
- `provider_models`: +`version`、+`probe_config_fingerprint`（可空）。
- `model_probes`: +`config_fingerprint`；`outcome` 约束加入 `PENDING`；+`ix_model_probes_outcome_started`。
- `provider_usage_records`: +`prompt_tokens_known`、+`completion_tokens_known`；迁移内回填并降级历史 `usage_known`。

升级前务必备份（命令见验收报告“数据库变更、备份与验证”）。降级到 `7b41c9d2f0aa` 会移除这些列并恢复旧 `outcome` 约束；若存在 `PENDING` 行，降级会被旧约束拒绝——需人工确认后处理，不要为通过降级删除调用证据。

**API 契约变更（向后不兼容，需注意）**

- `PATCH /api/v1/providers/{id}` 与 `PATCH /api/v1/models/{id}` 现在**必须**携带 `expectedVersion`，缺失返回 422，陈旧返回 409 `CONCURRENT_UPDATE`。
- 响应新增：`providers[].version`、`models[].version`、`models[].probeEvidenceStale`、`usage.partiallyMeasuredCalls`、`usage.*_unreported_calls`；探测 `outcome` 增加 `PENDING`。
- 新端点 `POST /api/v1/agents/{id}/model-unbind`（历史保留式解绑），返回该员工的履历。
- `providers[].notes`：`null` 表示清空，字段缺省表示保持不变。

**行为变更**

- 探测改为两阶段：调用前提交 `PENDING` 意图（独立事务），结果写入请求事务。中断的探测保持 `PENDING`，读作“结果与用量未知”，不自动重试。
- 供应商返回的模型名在持久化与返回前筛查当前凭据；命中即丢弃并标记 `metadata_dropped`。
- 配置变更（地址/类型/密钥引用/超时）使当前能力标记失效并保留历史；仅改名不失效。
- 用量汇总区分完整/部分/未计量；单维度缺失保持 `null`。

## 关键决定

沿用 [ADR-0003](../decisions/0003-phase-02-organization-and-model-centre.md)，并按其“验证与回退”一节落实。新增决定：

1. **`expectedVersion` 必填而非可选。** 可选版本等于允许“我不知道版本但仍然覆盖”，正是 R7 的缺陷。代价是所有既有调用方必须改；本仓库只有前端与验收脚本，已同步。
2. **指纹只覆盖影响调用的字段。** 名称、备注、状态变化不使探测证据失效，否则重命名会无谓地要求重新花 Token 测试。
3. **版本与指纹存在 `providers` 行上，不加配置历史表。** 本阶段只需要“证据是否属于当前配置”与“表单是否过期”，两者都不要求保留完整配置快照；旧探测行（含其指纹）已足以回答“当时用什么配置调用过”。
4. **中断即未知，不推断成功也不补记 0。** `PENDING` 是显式状态，不是“失败”或“成功”的别名。
5. **R6 用流式读取而非线程级取消。** `httpx` 的读超时是单次读；流式 + 逐块检查让配置值成为真预算，同时用体积上限封住无限流。
6. **R8 保留两步写入而不是合并为原子命令。** 资料编辑与模型绑定在审计上含义不同，合并会掩盖“谁在何时改了哪一半”。改为：第一步成功立即刷新版本，第二步失败明确告知范围并保留草稿。

## 验收

- 后端 **159 passed**（原 141 + 新 18），ruff/format/mypy 通过。
- 前端 **91 passed**（原 83 + 新 8），lint/typecheck/build 通过。
- 隔离 Compose 栈 **34/34** 端到端检查通过；浏览器 **20/20** 检查通过（含标签键盘与解绑持久化）。
- 迁移往返与旧数据回填实测通过。
- 真实浏览器 200% 缩放**未通过**（环境阻塞，诊断在 `evidence/phase-02/zoom-200-real.json`）；等效布局门禁通过但明确不算真实缩放。
- 真实供应商调用 **0 次**；真实进程强杀未执行（中断为注入异常模拟）。

## 下一阶段接入点

阶段 03 会直接依赖本轮改动，务必注意：

1. **适配器不再是“一问一答”**：`probe()` 现在可能抛 `AdapterErrorWithUsage`（`AdapterError` 子类），总是读取 `latency_ms`/`prompt_tokens`/`completion_tokens` 并记录用量。任务执行复用时不要只捕获 `AdapterError` 而丢掉用量。
2. **`PENDING` 必须被下游理解**：`model_probes.outcome` 现在有三态。任何统计、看板或预算逻辑不得把 `PENDING` 计入成功或记为 0 用量。阶段 03 的统一账本应把 `PENDING` 标为“未结算”。
3. **配置版本与指纹是并发控制的既有机制**：新的配置类端点（如阶段 03 的预算策略）应沿用同样的 `expectedVersion` 必填 + 409 契约，而不是重新发明。
4. **`provider_usage_records` 的维度标志**：并入统一账本时保留 `*_known` 语义，不要用 `or 0` 汇总。
5. **解绑是历史保留操作**：阶段 05 的执行记录若引用绑定 id，必须能处理“绑定已结束”的情况（`ended_at` 非空），不得把已结束绑定当成当前配置。

已知问题与建议先处理：

1. 真实浏览器 200% 缩放仍未验证；不要用 `--method=equiv` 记作通过。
2. 真实供应商连通性未验证。拿到密钥后应先在模型中心“测试连接”，确认 `resolved_name` 与 JSON 能力，再让阶段 03 依赖它。
3. 降级在存在 `PENDING` 行时需人工介入。
4. `vue-tsc` 对模板属性里的 `https://...` 字面量仍会破坏插槽作用域推断（见前端 `ModelsView` 的注释）；新表单请把示例 URL 放在脚本常量里。
