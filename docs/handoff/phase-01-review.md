# 阶段 01 质检修复交接

## 本阶段范围

完成代码和逻辑审查、原有检查复跑、6 项失败边界复现。未修改生产源码、接口或数据库结构；未开始阶段 02。质检结论为失败，详见 [独立质检报告](../acceptance/phase-01-review.md)。

## 工程入口

- 认证事务：`backend/app/api/deps.py`、`backend/app/db/session.py`、`backend/app/services/auth_service.py`。
- 日志：`backend/app/logging.py`。
- 前端会话：`frontend/src/api/client.ts`、`frontend/src/stores/session.ts`、`frontend/src/router/index.ts`。
- 页面：`SetupView.vue`、`SettingsView.vue`、`AppShell.vue`。
- 探针与原始结果：`docs/acceptance/evidence/phase-01-review/`。

## 变更

新增独立验收报告、本交接、后端探针、前端探针和两份测试输出。执行 `pnpm check` 重新生成 token 与 dist；无生产源码、迁移、配置或现有测试修改。

前端探针已从 `src/__tests__` 移至证据目录，避免未经修复就把预期失败的质检断言纳入日常测试。修复时应把它们整合进正式测试，并移除临时复制件。

## 关键决定

只质检，不直接实施修复。不调整架构和授权边界。测试使用独立 PostgreSQL；现有服务仅作状态及日志的只读检查。没有新增 ADR。

## 验收

- 原后端：47 passed；ruff、格式检查、mypy 全部通过。
- 原前端：44 passed；lint、typecheck、build 全部通过。
- 补充：后端 4 failed，前端 2 failed，对应报告 R1–R6。
- 浏览器视觉、全新部署、HTTPS、备份恢复本轮未执行。

### 复现命令（PowerShell）

测试容器已停止、保留；以下凭据仅用于本轮创建的独立测试容器，不能用于部署。

```powershell
docker start acos-review-pg-20261029
docker exec acos-review-pg-20261029 pg_isready -U acos -d postgres
# 等待上一步确认为 accepting connections 后继续。
cd backend
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://acos:review-local-test-only@127.0.0.1:55433/postgres'
./.venv/Scripts/python.exe -m pytest ../docs/acceptance/evidence/phase-01-review/backend_probes.py -q --tb=short --show-capture=no
```

前端在仓库根目录开始：

```powershell
Copy-Item -LiteralPath docs/acceptance/evidence/phase-01-review/frontend-probes.spec.ts -Destination frontend/src/__tests__/review-probes.spec.ts
cd frontend
pnpm exec vitest run src/__tests__/review-probes.spec.ts
# 复现结束后移除刚复制的文件，证据目录保留原件。
Remove-Item -LiteralPath src/__tests__/review-probes.spec.ts
```

这些测试断言预期正确行为，因此修复前应失败。前端 jsdom 的 scrollTo 提示属于测试环境限制；判定依据是最终路由不符，不是该提示。

## 下一阶段接入点

1. **先修 R1/R2**：成功响应必须晚于事务提交；默认及调试日志不得输出认证敏感参数。
2. **修 R3/R4**：登录限流原子化；过期撤销和审计有正确提交及去重边界。
3. **修 R5/R6**：401 同步会话状态、注销错误可恢复；初始化冲突刷新一次性状态。
4. 修平板导航名称/提示、验收脚本假通过、Worker 状态文案、START-HERE 过时内容。
5. 将已复现缺陷转为正式回归测试，补充注销提交失败测试，复跑已有检查；UI 修复后按规范重做截图与键盘检查。
6. 更新原阶段验收/交接的当前结论，保留历史证据。P1 未修前，不应把现有事务和日志方案作为已验收公共基础继续推广。
