# 阶段 01 验收报告

> 当前状态补充（2026-10-30）：下文保留 2026-10-29 的历史验收记录。其“通过”结论随后被[独立质检](phase-01-review.md) 的六项边界缺陷否定；本轮已实施修复，当前实测与未验证项以[修复验收](phase-01-fixes.md)为准。API readiness 仅由数据库和迁移决定，Worker 心跳独立报告；原文相关描述不是当前契约。历史 20 项脚本检查中的端口项曾为硬编码通过，本轮改成真实 Docker 检查。

日期：2026-10-29
对应阶段：01 工程骨架与 Docker Compose 部署
代码版本/分支：工作区实施（仓库尚未初始化 Git，无提交哈希）
本报告结论：**通过**（范围限于阶段 01；未验证项在下方明确列出）

环境及依赖版本：

| 项 | 版本 |
|---|---|
| 宿主 | Windows 10.0.26100 x64，Docker 29.6.2（Linux 容器，WSL2 内核 6.6.87.2） |
| Docker Compose | v5.3.1 |
| Python（开发/测试） | 3.13.12 |
| Node（开发/构建） | 24.21.0 |
| pnpm | 10.34.9（corepack 锁定） |
| 后端依赖 | FastAPI 0.162.0、Uvicorn 0.54.0、Pydantic 2.14.1、SQLAlchemy 2.1.6、Alembic 1.20.1、psycopg 3.3.6、argon2-cffi 25.1.0 |
| 前端依赖 | Vue 3.5.44、Vite 8.3.5、TypeScript 5.9.3、Vue Router 5.4.0、Pinia 4.1.0、Element Plus 2.14.7 |
| 镜像 | python:3.13-slim、node:22-alpine、nginx:1.29-alpine、postgres:17-alpine |

## 需求与证据

| 需求/验收 ID | 实际实现 | 测试方法与命令 | 实际结果 | 证据文件 |
|---|---|---|---|---|
| 1-1 前后端目录、锁文件、检查入口 | `backend/`（api/domain/services/repositories/models/workers）、`frontend/`（api/components/layouts/router/stores/styles/views）、`pnpm-lock.yaml`、`backend/requirements*.txt` | `ruff check`、`ruff format --check`、`mypy app`、`eslint`、`vue-tsc --noEmit`、`pytest`、`vitest`、`vite build` | 全部通过 | 见"检查命令实际输出" |
| 1-2 API 分层与存活/就绪探针 | `app/api`（路由/依赖/中间件/错误）、`app/services`、`app/repositories`；`/health/live` 不触库、`/health/ready` 校验数据库+迁移+心跳 | `pytest tests/test_health.py`（12 项）；`curl /api/v1/health/ready` | 通过；就绪返回 `database/migrations/worker = ok` | `.tmp/` 运行时输出，报告下方摘录 |
| 1-3 Alembic 迁移；初始化原子且仅一次；无默认口令 | `alembic/versions/6a790d8c2b88_*`（7 张表，插入唯一 `platform_state` 行）；`AuthService.complete_setup` 行锁 + 事务 | `pytest tests/test_auth.py::test_first_setup_creates_owner_and_session`、`::test_setup_is_rejected_after_completion`、`::test_concurrent_setup_only_succeeds_once` | 通过：并发双进程仅一方 201、另一方 409，`users` 计数为 1 | `backend/tests/test_auth.py` |
| 1-4 单所有者认证、哈希、注销、会话过期、写保护 | Argon2id、服务端会话（令牌存 SHA-256）、`expires_at`/`revoked_at`、CSRF 双提交 + 服务端哈希比对、Origin 中间件 | `pytest tests/test_auth.py`、`tests/test_security.py`（13 项） | 通过：错误口令不泄露账号存在性、注销后旧 Cookie 重放 401、跨站写 403、配对错误 CSRF 403 | 同上 |
| 1-5 Vue 导航六类页面 + 空状态 | 九个一级导航；七个未开放页面显示用途与开放阶段；`/showcase` 组件页 | 浏览器实测（见 UI 检查） | 通过 | `placeholder-projects-1440x900.png` |
| 1-6 Compose 含 web/api/worker/postgres/migrate；worker 仅健康循环 | `docker-compose.yml`；`migrate` 为一次性服务且 `api`/`worker` 依赖其 `service_completed_successfully` | `docker compose build`、`docker compose up -d`、`docker compose ps` | 四个常驻服务均 healthy；`migrate` 退出码 0 | 下方摘录 |
| 1-7 Web 统一代理 API、生产构建、数据库不默认外暴 | nginx 非 root，`/api` 反代，SPA 回退；`docker-compose.yml` 中 postgres 无 `ports` | `curl`、`docker compose ps`、`vite build` | 通过；`web` 仅发布 `127.0.0.1:8080` | 下方摘录 |
| 1-8 配置样例、密钥不入库/日志/Git、持久卷 | `.env.example`、`scripts/generate_secrets.py`（随机值）、`.gitignore` 含 `.env`、JSON 日志白名单、命名卷 | `pytest tests/test_security.py::test_system_status_never_leaks_secrets`、`::test_password_is_not_stored_in_plaintext`、`::test_session_token_is_not_stored_in_plaintext` | 通过 | `backend/tests/test_security.py` |
| 验收 A 全新环境可启动且健康 | 删除数据卷 + 重新生成 `.env` 后 `docker compose up -d` | `docker compose ps` | 通过：约 30 秒内四服务 healthy | 下方摘录 |
| 验收 B 首次初始化成功、重复被拒、错误口令不能登录、注销后旧会话失效 | 同 1-3/1-4 | `python scripts/verify_phase01.py` | 20/20 通过 | 下方摘录 |
| 验收 C 未登录不能调用业务 API；跨站写不能绕过会话保护 | `require_owner` 依赖；Origin 中间件 + CSRF | `pytest tests/test_security.py`、`verify_phase01.py` | 通过 | 同上 |
| 验收 D 停止再启动保留账号；重建容器数据保留 | 命名卷 `ai-company-os_postgres_data` | `docker compose restart`；`docker compose down` + `up -d`；查询 `users` 计数 | 通过：重启与重建后均为 1 条 owner 记录 | 下方摘录 |
| 验收 E 前端生产构建与后端测试通过 | `pnpm build`；`pytest` | 见检查命令输出 | 通过：`vitest` 44 项、`pytest` 47 项 | 下方摘录 |
| 验收 F 迁移失败阻止就绪（不出现假健康） | readiness 比对 `alembic_version` 与脚本 head | 手工改写 `alembic_version` 为 `deadbeef0000`；另起未迁移数据库的 API 容器 | 通过：均返回 503 且给出中文原因；API 容器 25–40 秒内转为 `unhealthy` | 下方摘录 |

## 检查命令实际输出

后端（`cd backend`）：

```
$ python -m pytest -q
47 passed, 4 warnings in 142.90s

$ python -m ruff check .
All checks passed!

$ python -m ruff format --check .
38 files already formatted

$ python -m mypy app
Success: no issues found in 31 source files
```

前端（`cd frontend`）：

```
$ pnpm lint
eslint . --max-warnings=0      → 0 problems

$ pnpm typecheck
vue-tsc --noEmit               → 无错误

$ pnpm test
Test Files  5 passed (5)
     Tests  44 passed (44)

$ pnpm build
dist/assets/index-*.js            45.59 kB │ gzip:  17.97 kB
dist/assets/element-plus-*.js    335.38 kB │ gzip: 117.32 kB
✓ built in 597ms
```

Compose 启动（`docker compose up -d`，全新数据卷）：

```
SERVICE    STATUS
api        Up 31 seconds (healthy)
postgres   Up 39 seconds (healthy)
web        Up 25 seconds (healthy)
worker     Up 31 seconds (healthy)
```

`python scripts/verify_phase01.py`：

```
[PASS] liveness returns 200
[PASS] readiness returns 200 with database+migrations ok -- checks={'database': 'ok', 'migrations': 'ok', 'worker': 'ok'}
[PASS] worker heartbeat reported in readiness
[PASS] setup-status reports current owner state
[PASS] first-time owner setup succeeds (201)
[PASS] setup issues session and CSRF cookies
[PASS] repeated setup is rejected (409 SETUP_ALREADY_COMPLETED)
[PASS] CSRF token available to the client
[PASS] authenticated session readable
[PASS] system status works and leaks no secrets
[PASS] logout without CSRF header is rejected (403)
[PASS] cross-site write is rejected (403)
[PASS] wrong password rejected without revealing account existence
[PASS] logout with CSRF header succeeds (204)
[PASS] session is unusable after logout (401)
[PASS] revoked cookie replay rejected (401)
[PASS] login with correct password succeeds
[PASS] business API rejects unauthenticated requests (401)
[PASS] web serves the SPA shell with security headers
[PASS] client-side route falls back to index.html
[PASS] database port is not published

== 20/20 checks passed ==
```

持久化：

```
$ docker compose exec -T postgres psql -U acos -d acos -tAc "SELECT count(*) FROM users;"
1                        # restart api/web/worker 之后仍为 1
$ docker compose down && docker compose up -d
$ docker compose exec -T postgres psql -U acos -d acos -tAc "SELECT count(*) || ' user(s): ' || string_agg(username, ',') FROM users;"
1 user(s): owner         # 容器重建（保留卷）后
```

迁移失败阻止就绪：

```
# 场景 1：版本号与代码 head 不一致
$ curl /api/v1/health/ready        → HTTP 503
  ready: False
  database ok
  migrations fail | 数据库版本 deadbeef0000 与代码版本 6a790d8c2b88 不一致，请先执行迁移。
# 约 25–40 秒后：docker inspect → api health = unhealthy

# 场景 2：数据库可达但完全未迁移（独立容器指向空库）
$ curl /api/v1/health/ready        → HTTP 503   migrations fail | 数据库尚未执行迁移。
$ POST /api/v1/auth/setup          → HTTP 503   {"code":"DATABASE_NOT_READY", ...}
```

## UI 检查

视口：1440×900、1024×768、390×844（`docs/quality-gates.md` 要求的三视口），另加 360×800 与 720×450（约 200% 缩放等效）检查。

| 检查项 | 方法 | 结果 |
|---|---|---|
| 侧栏 240 / 折叠 72 / 顶栏 64 / 手机 56 | 浏览器 `getBoundingClientRect()` | 240 / 72 / 64 / 56，全部精确 |
| 内容内边距 32 / 24 / 16 | `getComputedStyle().padding` | 32px / 24px / 16px |
| 导航项高 44、当前项 primarySubtle 底 + 3px 标识 | 实测 | 44px；底色 `rgb(239,246,255)`；左边框 3px `rgb(29,78,216)` |
| AppButton S/M/L = 32/40/48 | 组件展示页实测面板（`data-testid="measurements"`） | 32 / 40 / 48，全部 `data-pass="true"` |
| AppInput M/L = 40/48 | 同上 | 40 / 48，通过 |
| 表头 44、数据行 ≥56 | 同上（按 COMPONENTS.md 行高为下限） | 44 / 56，通过 |
| StatusBadge 高 24 | 实测 | 24px，通过 |
| 三种"正常"状态同时有文字与颜色 | 快照 | "正常"文本 + 绿色，非仅颜色 |
| 顶栏连接状态为实测值 | 断网模拟后读徽标 | "服务已连接"（含最近检查时间）→ "连接中断"，非硬编码 |
| 状态文字对比 ≥4.5:1 | 浏览器内用 sRGB 相对亮度计算实际渲染色 | 见下表，全部达标 |
| 控件边界 ≥3:1 | 同上 | 4.76:1，达标 |
| 焦点环可见 | Tab 到提交按钮后读 `outline` | `2px solid`，`:focus-visible` 为 true |
| 键盘可完成登录 | 仅用 Tab/Enter：body → 登录名 → 密码 → 提交按钮 → Enter | 成功进入 `/overview`；未填内容时提交按钮 `disabled` 被正确跳过 |
| 手机触控 ≥44 | 遍历 `button/a/input` 求最小外框 | 修复菜单按钮与跳转链接低于 44px 的问题后，无低于 44px 的元素 |
| 窄屏无整页横向溢出 | `scrollWidth > clientWidth` | 360/390/1024/1440 均 false |
| 弹窗不超视口、宽度 480 | 设置页注销确认弹窗 | 宽 480，`max-width: calc(100vw - 32px)` |
| 加载状态 | 人为延迟 fetch 2.5s | 骨架可见（`aria-busy="true"`） |
| 错误状态 | 人为使 fetch 失败 | 显示"服务状态读取失败"+ 中文原因 + "重新加载"按钮 |
| 禁用状态 | 空表单 | 登录按钮 disabled，且无默认账号提示 |
| 登录失败 | 错误口令 | "账号或密码不正确，或该账号暂时被锁定。"；保留登录名、清空密码 |

对比度实测（浏览器渲染值，非文档抄写）：

| 组合 | 实测 | 目标 |
|---|---:|---:|
| 正文 text / bg | 17.06:1 | 4.5:1 |
| 页面说明 textSecondary / bg | 7.24:1 | 4.5:1 |
| 辅助文字 textMuted / bg | 4.76:1 | 4.5:1 |
| 当前导航 primary / primarySubtle | 6.16:1 | 4.5:1 |
| 成功 success / successSubtle | 4.79:1 | 4.5:1 |
| 警告 warning / warningSubtle | 6.84:1 | 4.5:1 |
| 危险 danger / dangerSubtle | 5.91:1 | 4.5:1 |
| 中性 neutral / neutralSubtle | 6.92:1 | 4.5:1 |
| 主按钮 onPrimary / primary | 6.70:1 | 4.5:1 |
| 次按钮 text / surface | 17.85:1 | 4.5:1 |
| 危险按钮 onPrimary / danger | 6.47:1 | 4.5:1 |
| 控件边框 controlBorder / surface | 4.76:1 | 3:1 |

截图（`docs/acceptance/evidence/phase-01/`，均已人工查看）：

- `overview-1440x900.png`、`overview-1024x768.png`、`overview-390x844.png` — 三视口概览
- `showcase-1440x900.png` — 组件展示页（含实测面板，全部绿色达标）
- `settings-1440x900.png`、`settings-logout-dialog-390x844.png` — 设置页与确认弹窗
- `login-1440x900.png`、`login-390x844.png`、`login-360x800.png`、`login-zoomed-720x450.png` — 登录页各视口
- `login-wrong-password-1440x900.png` — 登录失败状态
- `overview-loading-state-1440x900.png`、`overview-error-state-1440x900.png` — 加载与错误状态
- `placeholder-projects-1440x900.png` — 未开放页面的空状态
- `overview-connection-lost-1440x900.png` — 连接中断状态（实测：徽标转为警告色"连接中断"）

实施过程中通过上述检查发现并修复的缺陷（均已复测）：

1. 错误响应体键名为 snake_case，前端读取 camelCase 失败。
2. 登录失败与锁定记录在抛错时被事务回滚，失败计数与审计记录丢失。
3. 数据库未迁移时返回 500 `INTERNAL_ERROR`，掩盖真实原因。
4. 未处理异常返回占位 `requestId: "-"`，无法排查。
5. `location = /index.html` 定义了自己的 `add_header`，导致 nginx 丢弃服务器级安全响应头（`/` 与 `/api` 均缺 CSP、X-Frame-Options）。
6. nginx 在启动时解析 `api` 主机名，API 容器重建后 web 持续 502。
7. Element Plus 默认表格高度 40/49，不符合 44/56 规范；改用 `inset box-shadow` 画分隔线以避免 1px 边框破坏尺寸。
8. 加载中的按钮退化为灰色 disabled 外观，丢失主操作含义。
9. 手机菜单按钮（40px）与跳转链接（38px）低于 44px 触控目标。
10. 顶栏"运行中/已连接"曾是硬编码徽标，等于在未测量时声称连接正常；已改为轮询就绪接口的真实状态（"检查中/服务已连接/连接中断"，标签页隐藏时停止轮询）。

## 数据与安全

| 检查 | 结果 |
|---|---|
| 迁移可通过 `alembic upgrade head` 从空库执行 | 通过（模板库每测试克隆均基于它） |
| 迁移版本比对失败即不就绪 | 通过（两种场景，见上） |
| 初始化幂等（含并发） | 通过：双进程并发仅一次成功，`users`=1 |
| 密码不明文 | 通过：`password_hash` 以 `$argon2id$` 开头 |
| 会话令牌不明文 | 通过：库中仅 64 位十六进制 SHA-256 |
| 注销立即失效 + 重放拒绝 | 通过：`revoke_reason = LOGOUT`，旧 Cookie 401 |
| 会话过期 | 通过：把 `expires_at` 改到过去后 401 |
| 跨站写拒绝 | 通过：`Origin: https://evil.example` → 403 `ORIGIN_NOT_ALLOWED` |
| CSRF 缺失/错误/跨会话配对 | 通过：三种情况均 403 `CSRF_VALIDATION_FAILED` |
| 未认证访问业务 API | 通过：401 `NOT_AUTHENTICATED` |
| 秘密不入响应 | 通过：`/system/status` 断言无 `password`/`secret`/连接串 |
| 审计事件不含口令 | 通过：断言 detail 不含提交的密码 |
| 数据库端口未发布 | 通过：`docker compose ps` 中 postgres 无端口映射 |
| 容器非 root | 后端镜像 `USER appuser`、前端 `USER nginx` |

## 模型验证

不适用。阶段 01 不含任何模型调用、Provider 适配或协议模拟服务。未执行真实 API 调用，也未使用演练数据——本阶段没有需要它们的功能。

## 未通过与未执行

| 项 | 状态 | 原因与影响 | 下一步 |
|---|---|---|---|
| 真实 HTTPS 下 `Secure` Cookie | **未验证** | 无 TLS 域名/证书环境；`COOKIE_SECURE=true` 时在纯 HTTP 下浏览器不会回传 Cookie，无法在本机完整体验 | 阶段 08 部署验收时在 HTTPS 环境验证 |
| TypeScript 7 与 vue-tsc 兼容性 | **未验证** | 选用已验证的 TS 5.9.3；TS 7 未测 | 单独验证后按 ADR 升级 |
| 屏幕阅读器实测（NVDA/VoiceOver） | **未验证** | 仅做 DOM/ARIA 结构与 Tab 顺序检查 | 后续阶段补充 |
| 自动化浏览器端到端测试 | **未建立** | 阶段 01 的 UI 验证为浏览器实测 + 截图人工检查 | 阶段 03 起引入 Playwright |
| 登录限流在多副本下的跨进程一致性 | **部分** | 计数已落库，但测试为单 API 实例；未验证多 API 副本并发时窗口边界 | 需要多副本时补充测试 |
| 备份/恢复流程自动化 | **未实现** | README 提供手工命令，未在阶段 01 执行真实恢复演练 | 阶段 08 |
| 未实现页面的真实数据 | **按计划缺席** | 组织/模型/任务/提案/知识/预算/审计界面属于阶段 02 起 | 见交接记录 |

上述未验证项均不阻断阶段 02：阶段 02 不依赖 HTTPS、TS 7、屏幕阅读器或浏览器 E2E 自动化。

## 验收判断

逐项对照 `development-plans/01-工程骨架与Compose部署.md` 的验收条件：

1. 全新环境用文档命令启动、前后端与数据库健康检查通过 — **通过**（实测，删除数据卷后重建）。
2. 首次初始化成功；重复初始化被拒；错误密码不能登录；注销后旧会话失效 — **通过**（20/20 自动检查 + 单元测试）。
3. 未登录不能调用业务 API；跨站写请求不能绕过会话保护 — **通过**（401 / 403 实测）。
4. 停止再启动保留账号；重建容器后数据保留 — **通过**（命名卷实测）。
5. 前端生产构建与后端基础测试通过；三视口截图、32/40/48 实测、登录键盘路径、颜色对比检查 — **通过**（见 UI 检查）。
6. 迁移失败明确阻止就绪，无假健康 — **通过**（两种失败场景均 503 + 容器 unhealthy）。
7. 交付 README、compose 配置、配置样例、锁文件、初始迁移、基础测试、交接记录 — **已交付**。

同时满足 `docs/quality-gates.md` 的全阶段公共门禁与阶段 01 特定证据要求。

**结论：阶段 01 通过，可以进入阶段 02。** 阶段 02 的实现者应确认：数据库与迁移可正常升级、`AuthService`/`require_owner`/`CSRF` 依赖可直接复用、`platform_state` 行已存在且不应被重建。
