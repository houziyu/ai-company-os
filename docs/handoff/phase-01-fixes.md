# 阶段 01 质检修复交接

日期：2026-10-30。对应[修复验收](../acceptance/phase-01-fixes.md)。

## 本阶段范围

已完成 R1–R6、平板导航、验收脚本真实端口检查、Worker 状态语义和过时入口文档修复；原六项失败探针已转为正式回归，并补充登录/注销提交失败、跨 app/未知用户并发限流、过期并发审计去重及注销错误重试等验证。未进入阶段 02，不含公司/员工/模型/任务/预算/执行器业务。

未完成的门禁：实际浏览器 200% 缩放；只完成了三视口、360px、720×450 等效重排和键盘/尺寸/局部对比度检查。其余未验证项、自动审批审查阻止的动作均见验收报告；不能依据本文件宣称阶段 01 全部通过。

## 工程入口

| 入口 | 当前责任 |
|---|---|
| `backend/app/api/deps.py`、`db/session.py` | function 作用域请求事务，响应发送前提交；引擎隐藏参数 |
| `backend/app/services/auth_service.py` | 单例锁内登录计数/验证/记录；会话行锁与过期撤销提交 |
| `backend/app/logging.py` | 事件文本/字段白名单，SQL WARNING，异常不含原始值 |
| `backend/tests/test_auth_regressions.py`、`test_logging.py`、`test_acceptance_script.py` | 正式新增 PG/日志/脚本回归 |
| `frontend/src/api/auth-failure.ts`、`client.ts`、`endpoints.ts` | 受保护 401 通知；公开登录/初始化命令不误清已有会话 |
| `frontend/src/stores/session.ts`、`composables/useLogout.ts` | 会话缓存与两个注销入口的反馈/重试 |
| `AppShell.vue`、`SettingsView.vue`、`SetupView.vue`、`LoginView.vue`、`OverviewView.vue` | 登录恢复、初始化冲突提示、导航名称/提示、API 状态语义 |
| `ConfirmDialog.vue`、`frontend/scripts/generate-tokens.mjs` | 实际弹窗根样式和 vh 单位；没有新 token 或第二套尺寸 |
| `scripts/verify_phase01.py` | HTTP 验收 + 实际 Docker 端口检查；缺证据即失败 |
| `scripts/verify_phase01_ui.cjs` | 仅访问隔离测试栈的 Chromium UI 检查与截图 |
| `deploy/compose.phase01-test.yml`、`scripts/phase01_test_stack.ps1` | 测试项目/标签/端口/卷隔离，避免使用原 `.env` |

实际后端检查（从 `backend`，使用现有 `.venv`）：

```powershell
./.venv/Scripts/python.exe -m ruff check . ../scripts/verify_phase01.py
./.venv/Scripts/python.exe -m ruff format --check . ../scripts/verify_phase01.py
./.venv/Scripts/python.exe -m mypy app
# 只指向独立测试 PostgreSQL；凭据是该测试容器的已有一次性测试配置。
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://acos:review-local-test-only@127.0.0.1:55433/postgres'
./.venv/Scripts/python.exe -m pytest -q --tb=short --show-capture=no
```

若该测试容器已停止，可先从根目录 `docker start acos-review-pg-20261029` 并确认 `pg_isready`。夹具只创建随机测试库，不指向真实数据库；不要为通过测试操作原 Compose 数据卷。

前端从 `frontend`：`pnpm check`。独立 Compose 从根目录：

```powershell
./scripts/phase01_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase01.py --base-url http://127.0.0.1:18081 --compose-project acos-phase01-fixes
# NODE_PATH 指向已有、含 playwright 的运行库目录；不需要变更前端依赖或锁文件。
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase01_ui.cjs
./scripts/phase01_test_stack.ps1 -ComposeArgs @('stop')
```

UI 脚本固定使用一次性测试账号（与验收脚本默认测试账号相同），会进行真实登录/注销；必须使用隔离端口。脚本不验证真实浏览器缩放，结果明确标识等效重排。以上固定测试凭据不可用于真实部署。

## 变更

没有新增表、索引、迁移、API 路径或响应字段。HTTP 成功只在提交成功后发送；提交失败沿用统一 503。过期撤销/审计会先提交再返回 401；已撤销会话不会重复写过期事件。默认应用日志内容更受限，第三方原始消息不再直接输出。

前端受保护请求默认参与 401 会话失效通知；公开认证命令显式 `authenticated:false`。会话 store 在作用域销毁时解除订阅。注销网络/服务错误会清空本地显示，但**不会把它等同于服务端已撤销**，页面保留不确定性提示和重试；401 可以正常结束注销。设置注销通过 SPA 路由进入登录，不再使用整页刷新。

初始化冲突跳转携带 `setup=completed`，用于登录页说明；这是展示信息，不赋予权限。导航与登录缓存不决定服务端授权。

升级/回退：没有 schema 变化，不需要数据回填；本轮未执行真实部署。操作真实环境前仍应按 README 备份 PostgreSQL，核对当前 Alembic revision、备份可读性/账号记录及独立环境恢复结果；备份恢复未在本轮演练。仅在另行授权部署时替换应用镜像，并验证 ready、登录、注销与日志。回退应用旧镜像会重新引入本轮已知缺陷，不应以回退数据库解决；不执行 downgrade、不删卷、不清历史证据。

## 关键决定

延续 [ADR-0002](../decisions/0002-phase-01-implementation.md)，补充澄清实际 API/Worker 分别就绪的语义，不改变技术路线：

1. 单所有者阶段用已有 `platform_state` 行锁串行登录（未知用户也走同一锁），避免新增全业务通用 JSON 表或仅靠内存锁。未来若拓展登录主体规模，应评估吞吐并制定新的数据库限流设计。
2. `DbSession` 的 function scope 是提交前响应的公共边界。未来流式响应不能在返回后继续使用这个已经关闭的会话。
3. `authenticate` 在依赖鉴权阶段、业务写入之前调用。过期处理含明确提交；不得在已有待提交业务写入后调用它，否则可能提交调用方无关工作。
4. 锁定/登录失败审计的原有提前提交继续保留；不把所有 4xx 一律提交，不用全局错误处理放宽事务一致性。
5. 不改 Worker readiness 政策来满足文案；API 可服务与后台健康分别表达。Worker 仍只是心跳进程。

## 验收

- 静态检查及 mypy 通过；完整后端 **65 passed**，最后相关增量 **9 passed**；前端 **55 passed**，lint/typecheck/build 通过。
- 全新隔离 Compose **21/21**，最后已有账号回归 **20/20**；四服务 healthy，迁移一次性退出 0。
- Chromium UI **21 项**，正式截图与结果见[证据索引](../acceptance/evidence/phase-01-fixes/README.md)。关键截图已用图片工具人工式视觉检查；不声称真人辅助技术验证。
- 实际 200% 缩放未完成，整体门禁部分完成；真实 TLS、备份恢复、依赖扫描、极端提交故障/负载等未验证项见验收报告。
- 原始 `phase-01-review` 报告、交接、复现代码及失败结果保持原样。原阶段报告只追加当前状态说明，历史输出保留。
- 本轮结束已停止新 QA 栈及独立 PG 容器，恢复开始时的停止状态；新旧卷与测试容器均保留，没有执行 down -v 或删除数据。停止输出见 `qa-stop.txt`。

## 下一阶段接入点

不自动开始阶段 02。先复核当前修复与未完成 UI 门禁，再由用户分配下一阶段。可以复用 function scope 的 `DbSession`、认证/CSRF 守卫、受保护 API 的 401 处理和统一注销交互，但必须遵守上面的提交/鉴权先后边界。

遗留风险：登录串行化未压测；旧版本日志中的敏感哈希不会因新版本止漏而自动消失；不要擅自删除历史日志或真实数据。没有新模型、预算或隔离安全能力的验收结论。
