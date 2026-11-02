# ADR-0002：阶段 01 依赖版本、认证与会话实现决策

日期：2026-10-29。状态：阶段 01 实施中采用的决策，随验收记录留档。

## 背景

ADR-0001 保留了若干开放事项，其中"具体依赖版本与 Python/pnpm 管理工具"要求阶段 01 验证后锁定。阶段 01 同时需要确定会话、CSRF、初始化幂等与就绪探针的具体实现方式。本 ADR 记录实际验证过的选择及理由。

## 依赖与工具版本锁定

全部版本在 2026-10-29 从 PyPI / npm / Docker Registry 查询后安装并实际运行，不是猜测。

后端（`backend/requirements.txt`）：FastAPI 0.162.0、Uvicorn 0.54.0、Pydantic 2.14.1、pydantic-settings 2.16.2、SQLAlchemy 2.1.6、Alembic 1.20.1、psycopg 3.3.6（binary）、argon2-cffi 25.1.0。镜像基础 `python:3.13-slim`。

前端（`frontend/package.json`）：Vue 3.5.44、Vite 8.3.5、TypeScript 5.9.3、Vue Router 5.4.0、Pinia 4.1.0、Element Plus 2.14.7。构建镜像 `node:22-alpine`，运行镜像 `nginx:1.29-alpine`。

数据库镜像 `postgres:17-alpine`。

**TypeScript 选择 5.9.3 而非 7.0.2**：`vue-tsc` 3.3.13 与 TS 7 的组合未经验证，阶段 01 优先选择可确认工作的组合。升级到 TS 7 需要单独验证并记录。

**pnpm 10.34.9 通过 corepack 锁定**（`packageManager` 字段 + 构建镜像内 `corepack prepare`），仓库只有 `pnpm-lock.yaml` 一个锁文件，没有 npm/yarn 锁文件并存。

**Python 依赖用 `requirements.txt` + `requirements-dev.txt` 而非 uv/poetry**：依赖数量少、无私有源、标准 pip 即可满足；引入额外工具不会带来本阶段需要的收益。测试依赖与生产依赖分文件，生产镜像只装 `requirements.txt`。

## 认证与会话

**密码哈希用 Argon2id**（time_cost=3、memory_cost=64MiB、parallelism=2）。未知用户路径也执行一次固定 dummy 哈希校验，使响应时间不可用于枚举账号。

**服务端会话，令牌只存哈希**。浏览器持有不透明随机令牌；数据库存 SHA-256。理由：注销必须立即失效（阶段验收要求"注销后旧会话失效"），纯 JWT 无法在不引入黑名单的情况下做到。会话记录带 `expires_at`、`revoked_at`、`revoke_reason`。

**CSRF 采用双提交 + 服务端哈希比对**。CSRF 令牌与某条会话绑定（`auth_sessions.csrf_token_hash`），通过非 HttpOnly 的 `acos_csrf` Cookie 交给 SPA，写请求必须在 `X-CSRF-Token` 头回传。仅比对 Cookie 与头会让攻击者用自己种下的 Cookie 通过，因此服务端与已认证会话记录做哈希比对：配对另一个会话的令牌同样被拒（有测试覆盖）。会话 Cookie 为 `HttpOnly` + `SameSite=Lax`。

**来源校验在中间件层**。所有 `/api/` 的非安全方法（POST/PUT/PATCH/DELETE）在进入路由前检查 `Origin`（缺失时退回 `Referer`）是否在允许集合内；集合锚定 `PUBLIC_BASE_URL` 并自动接受 localhost/127.0.0.1 变体。这一层独立于会话，因此 `POST /auth/setup`、`POST /auth/login` 这类尚无会话的写请求同样受保护。

**所有者初始化最多一次，由数据库保证**。初始迁移插入唯一 `platform_state` 行（`id = 1` + check 约束）；初始化事务 `SELECT ... FOR UPDATE` 锁该行后在同事务内创建用户、回填 `owner_user_id`、建立会话、写审计事件。并发初始化只有一方成功，另一方读到已填充状态返回 409（有双进程并发测试）。失败的用户创建事务整体回滚。

**登录尝试计数落库**，不放在内存，使重启不重置保护窗口。失败记录在抛错前提交，否则请求回滚会抹掉失败计数。

## 健康检查

2026-10-30 质检修正：实际 API `/health/ready` 的 `require_worker=False`，只以数据库与迁移为就绪门禁；Worker 心跳为独立诊断，其自身探针决定 Worker 健康。保留这一进程级设计，概览使用“API 可服务”，不再称“全部就绪”。下段原始决策描述中的“任一”只适用于启用 Worker 门禁的评估，不能当成 API 契约。

**存活与就绪分离**。`/health/live` 不触库，用于容器存活检查——否则数据库抖动会引发 API 重启风暴。`/health/ready` 检查数据库连通性 + `alembic_version` 等于代码中唯一 head + Worker 心跳新鲜度，任一不满足返回 503 并给出具体原因。

就绪检查失败时 API 容器必须变为 unhealthy（Compose healthcheck 使用 readiness），从而阻断 `web` 启动：不接受"网页能开但数据库没初始化"的假健康。已实测版本不一致与未迁移两种情况均返回 503 且容器转为 unhealthy。

**数据库错误映射为 503 而非 500**。`SQLAlchemyError` 注册独立处理器返回 `DATABASE_NOT_READY`。注意：为裸 `Exception` 注册的处理器由 Starlette 最外层中间件调用并会重新抛出，响应到不了客户端——因此必须为具体异常类型单独注册。

## 前端主题

**设计 token 单一来源 + 代码生成**。`docs/design/design-tokens.json` 经 `frontend/scripts/generate-tokens.mjs` 生成 `tokens.css`、`element-theme.css`、`tokens.ts`。生成物不提交（`.gitignore`），构建时重新生成。测试断言生成结果与 JSON 一致、组件中无硬编码 hex、组件样式无硬编码控件高度、引用的 `--acos-*` 变量都存在。这套测试在实施中实际发现了三处违规（展示页硬编码色值、两处硬编码高度、一个不存在的变量）并促成修复。

**控件尺寸用薄封装而非依赖 Element 默认值**。`AppButton`、`AppInput` 直接渲染原生元素并以 token 设定高度，因此 32/40/48 可被浏览器实测；组件展示页读取 `getBoundingClientRect()` 显示实际值，全部 7 项达标。

**表格尺寸必须覆盖 Element 默认值**。Element Plus 默认表格为 40/49px，不符合规范要求的表头 44、数据行 56。为此新增 `frontend/src/styles/tables.css`，是唯一允许触及 Element 表格内部类的文件。分隔线改用 `inset box-shadow` 而非 `border`，否则 1px 边框会把 44/56 变成 45/57。

**Element Plus 按组件注册**。整库注册产生 991 kB 单块；按需注册降到 335 kB。

**顶栏连接状态必须来自测量**。初版实现了硬编码的"运行中/已连接"徽标，等于在未测量时宣称健康，违反"不得伪造状态"。现改为 `useServiceHealth` 轮询 `/health/ready`，显示三态（检查中/服务已连接/连接中断）与最近检查时间，标签页隐藏时停止轮询。公司运行状态（暂停/恢复）在阶段 02 有真实功能后再加入，不预先占位。

**加载中的按钮保留变体配色**。仅"真正禁用"使用 disabled 配色；加载态只显示 spinner 与动作文本，否则主操作在提交期间变成灰色，丢失动作含义（实施中发现并修正）。

## 未采纳的选项

- JWT 会话：无法满足"注销后立即失效"。
- Cookie-only CSRF 校验：可被 Cookie 注入绕过。
- 内存登录限流：重启即失效。
- 全库 Element Plus 注册：包体积过大。
- 用 lifespan 在 API 启动时自动迁移：多副本会竞争迁移，且掩盖"迁移步骤失败"这一需要人工可见的部署事实。

## 遗留

- 未在真实 HTTPS 环境下验证 `Secure` Cookie。
- 未验证 TypeScript 7 与 vue-tsc 的兼容性。
- 未引入前端端到端（浏览器驱动）测试自动化；阶段 01 的 UI 验证为人工截图 + 浏览器内测量，记录在验收报告。
