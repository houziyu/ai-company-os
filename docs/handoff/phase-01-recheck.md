# 阶段 01 修复后复检交接

日期：2026-10-30。报告：[phase-01-recheck.md](../acceptance/phase-01-recheck.md)。

## 本阶段范围

完成 R1–R6 修复复核和前后端检查。原场景全部通过；新发现 R7（P2）：迟到的旧 401 会清掉用户重新登录后的新状态。没有实施修复或开始阶段 02。

## 工程入口

- `frontend/src/api/client.ts`：401 触发通知。
- `frontend/src/api/auth-failure.ts`：通知目前没有会话版本信息。
- `frontend/src/stores/session.ts`：无条件清会话，以及 refresh/login/setup/logout 状态写入。
- `docs/acceptance/evidence/phase-01-recheck/session-race.spec.ts`：独立、确定性的失败复现。

复现（从仓库根目录，先确认目标临时文件不存在）：

```powershell
Copy-Item -LiteralPath docs/acceptance/evidence/phase-01-recheck/session-race.spec.ts -Destination frontend/src/__tests__/recheck-session-race.spec.ts
cd frontend
pnpm exec vitest run src/__tests__/recheck-session-race.spec.ts
# 仅移除刚复制的临时文件，保留证据原件。
Remove-Item -LiteralPath src/__tests__/recheck-session-race.spec.ts
```

## 变更

只有独立复检报告、交接、复现探针和输出；没有生产源码/迁移/API 修改。运行 `pnpm check` 重新生成 token 和 dist。独立 PostgreSQL 容器恢复到停止状态，未删除卷。

## 关键决定

不将其他模型的验收结论直接当作本轮通过证据；实际重跑 65 个后端与 55 个前端测试。保留所有历史失败证据，不修改原探针以制造通过。

R7 的建议是会话代次校验，具体实现由修复者核对。必须保留服务端授权与有效 401 的失效行为，不通过整体屏蔽错误解决。

## 验收

- 后端 65 passed / 4 warnings；ruff、格式、mypy 全通过。
- 前端 55 passed；lint、typecheck、build 通过。
- 新探针 1 failed，见本轮证据目录。
- 上轮两个 P1 已确认修复；本轮未发现新的 P1。
- 实际浏览器 200% 缩放仍未完成，本轮没有浏览器视觉复验。

## 下一阶段接入点

先修 R7，将探针纳入正式测试；同时验证当前会话的 401 仍会清会话、旧会话的迟到错误不覆盖新状态、refresh 的迟到回写不会恢复已注销状态。重跑相关前端测试和完整检查。

按质量门禁完成实际 200% 缩放；有 UI 修改时补截图与键盘复验。更新当前验收状态，保留历史记录。不要自动进入阶段 02、推送或部署。
