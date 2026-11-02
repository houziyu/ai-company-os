# 阶段 01 独立代码与逻辑质检

日期：2026-10-29（Asia/Shanghai）  
对应阶段：01 工程骨架与 Compose 部署  
代码版本/分支：当前工作区，没有 `.git`，无法提供提交哈希或基于提交的差异审查。  
本报告结论：**失败：现有检查通过，但新增边界检查复现 6 项缺陷，其中 2 项 P1，应先修复再确认阶段验收。**

## 范围与环境

阅读了开工入口、架构、工程规范、阶段总纲与阶段 01、UI 规范与 token、质量门禁、ADR、原验收及交接；审查后端认证/事务/迁移/日志/健康、前端 API/会话/路由/公共组件，以及 Compose 和验收脚本。

实际实现仅到阶段 01。任务调度、预算、模型、执行器、审批、记忆尚未实现，不将这些计划内缺席列为本轮代码缺陷。未执行阶段 02 开发、生产部署或自动修复。

本轮使用现有 Python 虚拟环境和前端锁定依赖；测试 PostgreSQL 使用独立 `acos-review-pg-20261029` 容器、`postgres:17-alpine` 镜像、仅绑定 `127.0.0.1:55433`。测试夹具创建并清理独立随机库，未修改当前 Compose 数据库、账号或 `.env`。测试容器完成后已停止，保留以便复现。

## 需求与证据

| 检查 | 实际命令/方法 | 本轮结果 | 证据 |
|---|---|---|---|
| 后端静态检查 | backend 下 `.venv/Scripts/python.exe -m ruff check .` | 通过 | 工具实际输出 All checks passed |
| 后端格式检查 | `.venv/Scripts/python.exe -m ruff format --check .` | 通过，38 文件 | 工具实际输出 |
| 后端类型检查 | `.venv/Scripts/python.exe -m mypy app` | 通过，31 源文件 | 工具实际输出 |
| 原有后端测试 | 设置独立测试库连接后 `.venv/Scripts/python.exe -m pytest -q` | **47 passed，4 warnings，144.78s** | 本轮实际执行；警告涉及 TestClient、Alembic 配置与测试 Cookie API |
| 原有前端检查 | frontend 下 `pnpm check` | lint/typecheck 通过；**44 tests passed**；Vite build 通过 | 本轮实际执行，非引用原报告 |
| 后端补充边界检查 | `pytest ../docs/acceptance/evidence/phase-01-review/backend_probes.py -q --tb=short --show-capture=no` | **4 failed**，分别对应 R1–R4 | [测试输出](evidence/phase-01-review/backend-probes.txt)、[复现代码](evidence/phase-01-review/backend_probes.py) |
| 前端补充交互检查 | 临时置于 `src/__tests__/review-probes.spec.ts` 后执行 `pnpm exec vitest run src/__tests__/review-probes.spec.ts` | **2 failed**，分别对应 R5–R6 | [测试输出](evidence/phase-01-review/frontend-probes.txt)、[复现代码](evidence/phase-01-review/frontend-probes.spec.ts) |
| 运行服务只读检查 | `docker ps`、API 容器日志的布尔匹配检查 | 当前四服务 healthy；API 日志确有 SQL 参数及 token_hash 字段；未输出真实哈希值 | 本轮只读工具输出 |

补充测试断言的是应有行为，当前失败是缺陷证据；没有把红色测试当作验收通过。前端探针在运行后移到证据目录，不加入默认测试套件；生产代码未修改。`pnpm check` 重新生成了 token 和构建产物。

## 发现与修复建议

### R1 · P1：事务尚未提交，成功响应已经发出

位置：`backend/app/api/deps.py:32`；`backend/app/db/session.py:46–47`。

`DbSession` 使用默认作用域的 yield 依赖，提交在依赖退出时发生。所安装 FastAPI 的 `routing.py` 在 request 作用域退出前发送响应，因此初始化、登录和注销可以在 commit 成功之前向浏览器确认成功。提交时连接故障或数据库拒绝提交将无法收回已经发出的状态和 Cookie。

实测：保持真实数据库、迁移和初始化逻辑，仅在提交点注入数据库异常，得到 **HTTP 201、下发会话 Cookie、数据库 users=0**。同一事务封装也服务于注销，撤销失败时存在已经确认注销、旧会话仍有效的风险；本轮直接故障注入的是初始化接口。

建议：将事务提交放在成功响应发出之前（适配当前 FastAPI 的 function 作用域依赖，或明确在应用事务边界提交）；验证提交失败返回错误、不下发成功 Cookie。补充初始化、登录、注销提交失败测试，不能只检查 flush 成功。

### R2 · P1：默认日志输出认证敏感字段

位置：`backend/app/logging.py:46–50`；`backend/app/db/session.py:22–30`。

`configure_logging` 把 `sqlalchemy.engine` 设置成应用的 INFO 级别，数据库引擎没有隐藏绑定参数，导致 SQL 参数进入日志。JSON formatter 也不是注释宣称的字段白名单，而是接收任意非保留 extra 与消息内容。

实测：用测试账号初始化后，日志含 **Argon2 密码哈希、session token_hash、csrf_token_hash**。当前运行容器只读抽查也确认记录了 SQL 参数和 session 哈希字段。没有在报告或终端回显真实账号哈希。

影响：密码哈希被复制到权限和保留策略可能更宽的日志系统，增加离线猜解暴露面；会话元数据也泄漏。这里泄漏的是哈希，不是明文密码或可直接重放的会话令牌。

建议：生产 SQL 日志至少限制为 WARNING，设置参数隐藏，并落实真正的日志字段白名单/脱敏；对默认 INFO 和允许的 DEBUG 配置分别做敏感字段回归检查。

### R3 · P2：登录次数限制可被同一实例的并发请求越过

位置：`backend/app/services/auth_service.py:182–183`。

读取失败次数、验证密码和记录失败不在同一个串行化临界区。多个请求能一起读到阈值以下的旧计数，然后全部执行验证；仅把计数存入 PostgreSQL 不能解决此竞争。

实测：阈值设为 3，先失败 2 次；用真实数据库、真实密码验证和同一个 app 并发发起 4 次登录，在计数读后用屏障固定交错，得到 **[401,401,401,401]，持久化失败数=6**。正确结果应只有一次继续验证，其余触发 429。屏障只控制时序，没有改计数结果或放宽生产校验。

建议：对同一登录主体实行数据库锁或原子限流计数，包含未知用户名路径；增加单实例并发与跨实例并发测试。原交接把问题限定为“多 API 副本未验证”不准确，单实例线程并发即可发生。

### R4 · P2：会话过期审计及撤销原因被回滚

位置：`backend/app/services/auth_service.py:289–303`。

过期分支先写 `revoked_at`、`revoke_reason` 和 `SESSION_EXPIRED`，随后抛出 401。外层 `session_scope` 对异常执行 rollback，因此两项写入一起丢失。登录失败分支已经专门处理了这种事务边界，但过期分支没有。

实测：过期会话请求返回 401 后，**revoke_reason=null，SESSION_EXPIRED 审计数=0**。原有测试只断言 401，未验证持久化。

影响：过期会话仍会被 expires_at 检查拒绝，本问题不是过期认证绕过；问题是审计证据与撤销状态缺失、后续重复访问重复尝试写入又回滚。

建议：给过期状态转换设计明确的提交边界，保证先持久化再返回认证失败，并验证重复访问只记录一次事件。

### R5 · P2：会话失效后，前端仍拒绝进入登录页

位置：`frontend/src/api/client.ts:142–144`；`frontend/src/router/index.ts:122,158–159`；`frontend/src/views/SettingsView.vue:34–41`。

受保护接口返回 401 时只转成页面错误，没有同步清除会话 store。路由只在 `state==='unknown'` 时复查会话；旧 authenticated 状态把 `/login` 又重定向到 `/overview`。长时间打开标签页或其他窗口注销后会触发。

实测：设置页面收到真实错误类型 `NOT_AUTHENTICATED` 后，在同一 SPA 中跳转 `/login`，最终路径仍是 **/overview**。用户需要整页刷新才能恢复登录流程。

相关失败路径：顶部注销和设置页注销都没有 catch；请求失败时 store 的 finally 清除本地状态，但跳转语句不会执行，也没有可见错误说明。若是网络失败，服务器会话可能仍有效，不能把本地清除当成服务端已注销。

建议：集中处理受保护接口的 401、同步会话状态并提供重新登录路径；401 注销可按已无有效会话处理，网络失败则明确提示未知结果并允许重试。

### R6 · P2：并发初始化失败后，旧缓存阻止登录跳转

位置：`frontend/src/views/SetupView.vue:68–70`；`frontend/src/router/index.ts:144–145`。

两个独立浏览器同时打开首次初始化页，其中一个创建成功后，另一个提交收到 `SETUP_ALREADY_COMPLETED`。错误处理虽然请求跳转 `/login`，但没有刷新 `setupComplete=false` 的缓存，路由守卫把它重新导回 `/setup`。

实测：使用真实 SetupView、store 和生产路由守卫，仅模拟 API 的 409 返回，最终路径是 **/setup**。后端的初始化行锁正确，并不代表整个浏览器流程正确。

建议：收到该确定状态时更新或重新读取 setup-status，然后进入登录页，并保留用户可理解的提示。

## 静态检查补充项

这些项目有明确源码依据，但本轮未进行真实浏览器或全新部署复现，不与上面的 6 项实测混为一类。

- **平板导航可访问性（P2）**：`AppShell.vue:155–168,488–489`。768–1279px 下导航文字 `display:none`，图标 `aria-hidden`，按钮未提供 `aria-label`；`isNarrow` 只匹配小于 768px，导致图标模式的 tooltip 也被禁用。设置按钮同样没有独立可访问名称。应给每个按钮明确名称，提示根据折叠状态启用，并在 1024px 做辅助技术/键盘验证。
- **验收脚本硬编码通过**：`scripts/verify_phase01.py:374–376` 的“数据库端口未发布”直接传入 True，没有实际检查 Docker。当前配置确实没发布数据库端口，但脚本无法发现以后误发布端口的回归。应实际检测，或标记为人工/未执行，不计入自动通过数。
- **状态语义与文档不一致**：`health.py:35` 使用 `require_worker=False`，Worker 故障不阻断 API readiness；概览却把 ready 显示为“全部就绪”。原验收/交接称 Worker 心跳也决定就绪，与代码不符。可以保留 API 和 Worker 分别健康的设计，但 UI 应区分 API 可服务和全栈正常，文档需匹配。
- **工程入口文档过时**：`docs/START-HERE.md` 仍称没有正式 FastAPI/Vue/Compose 工程，与 README 和阶段交接矛盾。应在修复验收后同步实际阶段状态。

## UI 检查

本轮无 UI 实现变更，新增的是 jsdom 中的真实组件与路由交互探针。**没有重跑三视口截图、200% 缩放、对比度、键盘焦点或屏幕阅读器测试**；原截图和验收记录仅作为背景，不能作为本轮实测。

## 数据与安全

初始化一次性行锁、密码哈希、会话令牌哈希、CSRF 与 Origin 防护已有实现，现有对应测试通过。新增检查暴露响应与事务边界、日志保密、并发限流、过期审计的问题；这些是阶段 01 基础能力，不应推到预算/审批阶段才修。

本轮未执行真实数据清理、数据库降级、卷删除、运行栈重建或故障注入。故障注入仅发生在独立测试 app/数据库中。

## 模型验证

不适用。无演练、模拟模型 API 或真实模型调用；当前阶段未实现 Provider。

## 未通过与未执行

- 未通过：R1–R6，共 6 个补充断言；需修复并转为长期回归测试。
- 未执行：全新 Compose 安装、镜像重建、真实 TLS、断电/网络中断下的提交、备份恢复、三视口浏览器复验。
- P1 提交失败使用可控故障注入，未声称已覆盖所有外部故障；并发限流使用屏障稳定复现合法交错，未进行压力测试。
- 本轮没有依赖漏洞扫描，也未对尚未实现的任务/预算/权限委托能力作安全结论。

## 验收判断

基础工程及正常路径有实际实现，已有检查可运行且全部通过，但不足以支持原报告的无阻断“通过”判断。建议先处理 **R1 提交边界、R2 日志敏感字段**，随后修复 R3–R6 和静态补充项，再回归阶段 01；通过后再把认证和事务封装交给下一阶段复用。

修复交接见 [phase-01-review.md](../handoff/phase-01-review.md)。本报告保留原验收文件，作为独立复核补充，不改写历史证据。
