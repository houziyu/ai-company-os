# 阶段 01 R7 修复证据

日期：2026-10-30。[验收](../../phase-01-r7-fixes.md)、[交接](../../../handoff/phase-01-r7-fixes.md)。

- `probe-before.txt`：按原探针稳定复现，1 failed。
- `related-tests.txt`：包含原探针的五个正式测试文件，46 passed。
- `frontend-checks.txt`：完整 `pnpm check`，74 passed，lint/typecheck/build 通过。
- `backend-r1-r4.txt`：独立 PG 下 12 项旧缺陷回归通过；没有修改后端。
- `test-db-stop.txt`：测试结束停止独立 PG，容器/卷保留。
- `preserved-evidence-sha256.json`：原复检报告、交接、探针与失败输出的交付时校验和，便于后续确认原始证据未被改写。
- `ui-checks.txt`、`ui-results.json`：真实构建 SPA + 内存 API 测试夹具，8 项检查，无未捕获页面异常。夹具不访问真实数据库，显示名/服务详情均注明模拟 API。
- `login-*`、`overview-*`、`settings-dialog-*`：1440×900、1024×768、390×844、360×800，每视口3张，共12张。三视口概览与360px弹窗已视觉检查，无新增裁切/遮挡。
- `zoom-attempt.txt`、`zoom-status.json`：实际200%缩放尝试与阻塞说明；只有100%基准测量，没有200%通过证据，也没有以缩小视口代替。

原始复检证据仍在相邻 `phase-01-recheck/`，未修改；正式回归位于 `frontend/src/__tests__/session-race.spec.ts`。本目录的模拟浏览器结果不能替代真实后端认证、真实网络乱序或实际200%缩放验证。
