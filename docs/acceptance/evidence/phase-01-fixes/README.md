# 阶段 01 修复证据索引

日期：2026-10-30；[验收](../../phase-01-fixes.md)、[交接](../../../handoff/phase-01-fixes.md)。原始质检证据在相邻 `phase-01-review/`，未修改。

- `backend-before.txt` / `frontend-before.txt`：原探针修复前 4+2 项失败，屏蔽失败时的原始日志捕获，避免再次复制认证敏感字段。
- `backend-checks.txt`：完整后端 65 passed；`backend-final-delta.txt`：最后日志/验收脚本相关 9 passed。
- `backend-static.txt`：最终 ruff、格式、mypy；`frontend-checks.txt`：最终 tokens/lint/typecheck/55 tests/build。
- `backend-regressions.txt` / `frontend-regressions.txt`：开发过程中的新增回归通过结果；最终结果以完整检查文件为准。
- `compose-build.txt`：首次全新隔离栈构建/启动；`compose-final-build.txt`：最终镜像；`compose-ui-rebuild.txt`：UI 迭代构建。
- `compose-acceptance.txt`：新栈 21/21；`compose-regression.txt`：已有账号的最终 20/20，首次初始化专项没有伪造执行。
- `qa-stack-health.txt`：测试时四服务 healthy 与迁移退出 0；`original-stack-state.txt`：原服务仍处于原停止状态。
- `qa-stop.txt`：测试结束后停止新 QA 栈与独立 PG；容器及卷保留，没有删除数据。
- `runtime-log-check.json`：只保存敏感字段泄漏检查布尔值，不保存原始运行日志。
- `ui-checks.txt`、`ui-results.json`：最终 Chromium 21 项检查；`contrast-results.json`：13 个实际渲染组合（含输入边界/焦点）。

截图按测试视口命名，长页面为 fullPage：

| 文件 | 覆盖 |
|---|---|
| `login-disabled-{1440x900,1024x768,390x844,360x800}.png` | 默认/禁用、三视口及360px |
| `overview-empty-{1440x900,1024x768,390x844,360x800}.png` | 服务事实与业务空状态 |
| `login-error-1440x900.png`、`login-submitting-1440x900.png` | 错误与关键提交 |
| `tablet-navigation-focus-tooltip-1024x768.png` | 图标模式的可访问名称与聚焦提示 |
| `mobile-navigation-390x844.png` | 导航抽屉 |
| `settings-logout-dialog-390x844.png`、`settings-logout-database-error-390x844.png` | 注销确认和明确的失败反馈 |
| `topbar-logout-network-error-390x844.png` | 顶栏注销网络失败和重试 |
| `session-invalidated-1440x900.png` | 实际服务器撤销会话后收到401，提供登录入口 |
| `setup-conflict-login-1440x900.png` | 实际409后进入登录及说明 |
| `showcase-measurements-1440x900.png` | 公共组件尺寸与状态展示 |
| `overview-loading-1440x900.png`、`overview-error-1440x900.png` | 加载、错误与重试 |
| `overview-reflow-720x450.png` | 等效重排，不是实际200%缩放 |

以上为21张验收截图；另有 `zoom-attempt-actual-100.png` 是失败的缩放配置尝试所留原始截图，不计入验收。总计22张。关键三视口、移动失败弹窗、聚焦提示、初始化/会话恢复截图已人工式视觉审阅，没有把截图生成等同于真人屏幕阅读器或真实200%缩放检查。
