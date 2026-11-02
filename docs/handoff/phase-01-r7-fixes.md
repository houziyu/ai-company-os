# 阶段 01 R7 修复交接

日期：2026-10-30。对应[验收报告](../acceptance/phase-01-r7-fixes.md)。

## 本阶段范围

已增量修复 R7：迟到的旧 401 不再清除新登录状态；统一保护 refresh、登录、初始化、注销和 setup-status 的异步回写。原独立探针已转正式回归并扩展为 19 项时序测试。未进入阶段 02。

本轮相关测试、前端完整检查和三视口/360px 浏览器夹具检查通过；实际浏览器 200% 缩放受 URL 安全检查阻止，仍未验证，因此完整门禁部分完成。

## 工程入口

| 文件 | 当前职责 |
|---|---|
| `frontend/src/api/auth-failure.ts` | 浏览器内会话代次及通知；仅匹配发起代次的 401 才广播 |
| `frontend/src/api/client.ts` | fetch 发起时捕获代次，错误仍正常拒绝给调用者 |
| `frontend/src/stores/session.ts` | 会话切换推进代次，认证操作序号限定结果归属；读取响应需同时匹配两个序号 |
| `frontend/src/__tests__/session-race.spec.ts` | 原探针全部行为断言及 18 项新增时序回归 |
| `scripts/verify_phase01_r7_ui.cjs` | 构建 SPA + 明确标注的内存 API 夹具；不访问数据库，仅监听本机 18082 |

从 `frontend` 执行：

```powershell
pnpm exec vitest run src/__tests__/session-race.spec.ts src/__tests__/session-store.spec.ts src/__tests__/api-client.spec.ts src/__tests__/auth-regressions.spec.ts src/__tests__/logout-regressions.spec.ts
pnpm check
```

本轮后端相关回归（先确认独立测试 PG 已就绪，从 `backend`）：

```powershell
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://acos:review-local-test-only@127.0.0.1:55433/postgres'
./.venv/Scripts/python.exe -m pytest tests/test_auth_regressions.py tests/test_logging.py -q --tb=short --show-capture=no
```

本轮结束独立 PG 容器已恢复停止状态，后续需要时仅启动该独立容器；不要指向真实库或清理任何现有卷。前端浏览器夹具无需 PG（从根目录）：

```powershell
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase01_r7_ui.cjs
```

`--zoom-200` 模式会打开专用 Chrome 窗口，等待真实页面 DPR 倍增、outerWidth 不变且 CSS 视口宽度减半后才继续；不会自动伪造缩放。当前 Computer Use 无法可靠确定 URL，实际缩放未执行。该入口可供后续在可用控制环境复验，不能把启动窗口记为通过。

## 变更

生产代码限于三个前端文件；无后端、接口、schema、Cookie、依赖、token 或视觉组件变更。新增正式测试、前端独立浏览器测试脚本与本轮证据文档。

1. `applySession`、显式清会话、服务端确认失效和注销边界推进会话代次。请求捕获旧代次后返回的 401 仍抛给其调用者，但不再广播清除新会话。
2. 成功 refresh 只确认同一会话，**不推进代次**，否则可能错误忽略同一会话仍有效的其他在途 401。
3. refresh 与 setup-status 捕获会话代次和认证操作序号，迟到成功、401 或网络错误不能改写后续登录/注销状态。
4. 凭据认证命令有独立操作序号。旧 Cookie 的有效 401 不会取消进行中的合法登录；登录成功后再推进会话代次。否则只使用单一序号会引入另一种竞态。
5. 注销开始即推进代次，使旧读取失效；最后清理核对操作归属。保留 R5 的“服务端注销结果未知”提示、当前 401 正常失效与公开登录命令错误不误清会话的行为。

兼容性：接口与数据无需迁移。会话代次只存在于当前 SPA，不改变服务端权限，不提供跨标签页 Cookie 写入顺序保证。不执行真实部署或数据库回退。

## 关键决定

沿用既有架构与 [ADR-0002](../decisions/0002-phase-01-implementation.md)，没有技术路线变更。采用“会话切换代次 + 认证命令顺序”，而非临时屏蔽登录后若干秒的 401；所有错误仍由调用者收到。原探针只增加字面量类型标注及格式整理，没有删减断言。

本轮没有修改 Vue 页面/公共组件，浏览器检查仍覆盖相关登录/注销交互和布局；模拟 API 仅在独立测试脚本内，并明确标识，不进入正式服务镜像。

## 验收

- 原探针修复前 1 failed，修复后随正式回归通过；新增时序文件共 19 项。
- 五个相关前端文件 **46 passed**；`pnpm check` **74 passed**，tokens/lint/typecheck/build 全通过。
- R1–R4 PostgreSQL/日志回归 **12 passed**，2 个既有依赖弃用警告。
- 构建后的 SPA 在三视口及 360px 下，内存 API 夹具 **8 项**浏览器检查通过、12 张截图；关键截图已用图片工具检查。没有把夹具当成服务端验证。
- 实际 200% 缩放未验证：选择专用窗口时 Computer Use 因无法可靠识别 URL 而停止；不再发送桌面输入。详情见验收与[证据目录](../acceptance/evidence/phase-01-r7-fixes/README.md)。
- 原 `phase-01-recheck.md` 报告、交接、探针及输出保持原样；原 R1–R6 证据也没有改写。

## 下一阶段接入点

仍停留阶段 01。先复核本轮代码、完成实际 200% 缩放等未验证门禁；后续阶段需用户另行分配。

新增共享数据异步回写若可能跨越会话切换，应核对归属；不要在 refresh 成功时机械推进代次，不要在任意 401 后取消正在进行的合法凭据认证。跨标签页同步、真实网络乱序、并发认证 Cookie 落地和完整后端/Compose 复验都没有新增“已验证”结论。
