# 阶段 01 交接

> 当前交接补充（2026-10-30）：本文件保留原始交接背景；认证提交边界、日志、限流、过期审计与前端恢复流程已经修复，接手时必须先读 [phase-01-fixes.md](phase-01-fixes.md)。当前为后端 65 项、前端 55 项回归测试；API readiness 检查数据库和迁移，Worker 独立健康，不会阻断 API readiness。原“无阻断通过”结论被独立质检否定后的处理和剩余 UI 门禁见修复验收，不据原文直接进入阶段 02。

交接对象：阶段 02（公司组织与模型中心）的实施者。本文件可独立阅读，不需要聊天历史。

## 本阶段范围

**已完成**

- 前后端工程骨架、依赖锁文件、lint/typecheck/test/build 入口。
- 设计 token 生成链路：`docs/design/design-tokens.json` → CSS 变量 + Element Plus 主题映射 + TS 常量。
- 公共组件：`AppButton`、`Input`（AppInput）、`FormField`、`StatusBadge`、`PageHeader`、`EmptyState`、`ErrorState`、`ConfirmDialog`、`AppDrawer`、`AppShell`、`AuthLayout`。
- 组件展示页 `/showcase`（含浏览器实测尺寸面板）。
- 中文工作台外壳：九个一级导航，七个未开放页面显示用途与开放阶段。
- 单所有者初始化（原子、仅一次）、登录、注销、会话过期与撤销、登录失败锁定、CSRF 与来源校验。
- 存活/就绪探针；API 就绪校验数据库 + 迁移版本，独立展示 Worker 心跳。
- Compose 全栈：`postgres`、`migrate`（一次性）、`api`、`worker`、`web`；健康检查、内部网络、命名卷。
- 初始 Alembic 迁移（7 张表）。
- 47 项后端测试、44 项前端测试、20 项端到端验收脚本。

**明确不在本阶段范围（未实现，不要误认为已有）**

- 创建公司/公司模板实例化、组织与员工、模型供应商与分配。
- 任务、执行、预算账本、队列、隔离执行器。
- 提案、审批、知识、审计界面。
- 任何模型调用或 Provider 适配。
- 在线备份与恢复、产物卷。

## 工程入口

### 目录与关键模块

```
backend/app/
  config.py                     Settings（环境变量、来源白名单推导）
  logging.py                    JSON 结构化日志（字段白名单）
  asgi.py                       uvicorn 入口：app = create_app()
  main.py                       create_app() 工厂 + 中间件与路由装配
  api/context.py                request_id 上下文、客户端地址
  api/middleware.py             RequestContextMiddleware、OriginGuardMiddleware
  api/errors.py                 统一错误响应；SQLAlchemyError → 503
  api/deps.py                   get_db / require_owner / require_csrf
  api/cookies.py                会话与 CSRF Cookie 写入/清除
  api/schemas.py                v1 请求/响应模型（camelCase 别名）
  api/v1/auth.py                setup-status / setup / login / logout / session
  api/v1/health.py              health/live、health/ready、system/status
  domain/enums.py               角色、会话撤销原因、审计动作
  domain/errors.py              AppError 及子类（带 HTTP 状态与中文文案）
  domain/security.py            Argon2id、令牌生成与哈希、密码策略
  repositories/auth_repository.py  Platform/User/Session/LoginAttempt/Audit/WorkerHeartbeat 数据访问
  services/auth_service.py      初始化、登录、会话解析、CSRF 校验、注销
  services/health_service.py    就绪评估、system status 组装
  models/entities.py            全部表定义
  workers/health_worker.py      阶段 01 心跳 Worker（无业务）
backend/alembic/versions/6a790d8c2b88_phase_01_initial_schema.py
frontend/src/
  api/client.ts                 fetch 封装：CSRF 头、ApiError/NetworkError
  api/endpoints.ts              接口分组封装
  api/types.ts                  与后端 schema 对应的类型
  stores/session.ts             会话状态（不自行推断权限）
  router/index.ts               路由 + 守卫（setup/login/未登录重定向）
  router/navigation.ts          导航元数据与各页开放阶段（单一来源）
  layouts/AppShell.vue          桌面/平板/手机三态外壳
  layouts/AuthLayout.vue        440px 居中认证卡
  components/                   公共组件
  styles/{tokens,element-theme}.css, tables.css, base.css
  views/                        登录、初始化、概览、设置、占位页、组件页、404
```

### 实际可用的启动与检查命令

```bash
# 部署
python scripts/generate_secrets.py         # 首次，生成 .env
docker compose up -d
docker compose ps

# 端到端验收（对已启动栈，20 项）
python scripts/verify_phase01.py --base-url http://127.0.0.1:8080

# 后端
cd backend
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app
./.venv/Scripts/python.exe -m pytest -q          # 需 PostgreSQL，见 README

# 前端
cd frontend
pnpm tokens && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

`pytest` 需要真实 PostgreSQL。默认 `TEST_ADMIN_DATABASE_URL=postgresql+psycopg://acos:devpass@127.0.0.1:55432/postgres`，可用临时容器：

```bash
docker run -d --name acos-pg-dev -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_USER=acos -e POSTGRES_DB=acos -p 55432:5432 postgres:17-alpine
```

### 配置变量（只列名称与含义，不含值）

`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`、`DATABASE_URL`、`APP_ENV`、`APP_VERSION`、`LOG_LEVEL`、`PUBLIC_BASE_URL`、`EXTRA_ALLOWED_ORIGINS`、`SESSION_TTL_HOURS`、`COOKIE_SECURE`、`PASSWORD_MIN_LENGTH`、`LOCKOUT_THRESHOLD`、`LOCKOUT_MINUTES`、`WEB_BIND_ADDRESS`、`WEB_PORT`、`WORKER_NAME`、`WORKER_HEARTBEAT_INTERVAL_SECONDS`、`ENABLE_API_DOCS`。

## 变更

### 数据表（迁移 `6a790d8c2b88`）

| 表 | 用途 | 阶段 02 注意 |
|---|---|---|
| `platform_state` | 单行（`id=1`），记录 `owner_user_id`/`owner_created_at` | **不要重建或删除该行**；迁移已插入空行，初始化只做填充。若新增公司级字段，加在此表或新建 `companies` 表 |
| `users` | 所有者账号；`role` 约束当前只允许 `OWNER` | 阶段 02 若引入员工/多角色，需要新增迁移放宽 check 约束，不能直接改模型 |
| `auth_sessions` | 服务端会话（令牌 SHA-256、CSRF SHA-256、过期、撤销） | 直接复用 |
| `login_attempts` | 失败计数来源 | 直接复用 |
| `audit_events` | 追加型审计；`actor_user_id` 可空 | 阶段 02 起应在组织/模型变更时写入；不要添加删除入口 |
| `worker_heartbeats` | Worker 心跳（`worker_name` 唯一） | 阶段 03 起真实 Worker 复用同一表 |

### API

已存在的 v1 路由：`GET /auth/setup-status`、`POST /auth/setup`、`POST /auth/login`、`GET /auth/session`、`POST /auth/logout`、`GET /health/live`、`GET /health/ready`、`GET /system/status`、`GET /meta`。

约定（新增接口必须遵守）：

- 统一错误体 `{code, message, requestId, fieldErrors[], details?}`，`message` 为可展示中文。
- 写接口一律使用 `WriteGuard`（登录 + CSRF）；只读业务接口使用 `AuthenticatedDep`。
- 响应模型继承 `ApiModel`（自动 camelCase 别名）。
- 列表接口必须分页且有稳定排序；写命令需要幂等键（阶段 03 起强制）。

### UI 组件与 token

- 阶段 01 建立的公共组件清单见 `docs/design/COMPONENTS.md` §8 的前半部分（均已实现）。
- 新增 `frontend/src/styles/tables.css`：唯一允许覆盖 Element 表格内部类的文件。新表格不要另写尺寸，继承它。
- token 由 JSON 生成；改设计值只能改 `docs/design/design-tokens.json` 再 `pnpm tokens`。`design-tokens.spec.ts` 会扫描硬编码 hex 与控件高度。
- 新增 Element 组件时必须在 `frontend/src/main.ts` 显式注册（按需注册，不整体 `use(ElementPlus)`）。

### 升级与回退边界

- 迁移可回退（`alembic downgrade base` 会删表），但**不得**在有真实数据的环境执行降级；阶段 01 无业务数据，降级仅用于开发环境。
- 镜像更新用 `docker compose up -d --build`。`docker compose down -v` 会删除数据卷，禁止作为升级手段。
- 配置键名变更属于破坏性变更，需同时更新 `.env.example`、`README.md` 与 `Settings`。

## 关键决定

见 [ADR-0002](decisions/0002-phase-01-implementation.md)。要点：

1. 依赖与镜像版本已按实测锁定（含为何选 TS 5.9.3 而非 7）。
2. 服务端会话 + 令牌哈希存储，满足"注销立即失效"。
3. CSRF 为会话绑定的双提交 + 服务端哈希比对（Cookie-only 可被绕过）。
4. 初始化一次性由 `platform_state` 单行 + 行锁 + 主键约束保证。
5. 存活/就绪分离；数据库错误映射 503 而非 500；必须按具体异常类型注册处理器。
6. Element Plus 按组件注册（991 kB → 335 kB）；表格尺寸在 `tables.css` 覆盖，分隔线用 inset shadow 以保持 44/56。
7. 加载态按钮保留变体配色，只有真正禁用的按钮使用 disabled 配色。

## 验收

见 [docs/acceptance/phase-01.md](acceptance/phase-01.md)。

- 结论：通过。
- 实际通过：47 后端测试、44 前端测试、20 项端到端检查、三视口/360px/缩放 UI 检查、对比度实测、键盘登录、持久化与迁移失败门禁。
- 未验证：HTTPS 下 Secure Cookie、TS 7 兼容性、屏幕阅读器实测、浏览器 E2E 自动化、多 API 副本下的限流一致性、备份恢复演练。

## 下一阶段接入点

### 可直接复用

- `require_owner` / `AuthenticatedDep`：所有需登录的只读接口。
- `WriteGuard`：所有写接口（已含 CSRF 校验）。
- `ApiModel` 与统一错误处理器：新增 schema 与错误只需抛 `AppError` 子类。
- `session_scope` + function 作用域的 `DbSession`：成功响应发送前提交；跨表事务在同一 `Session` 内完成。认证拒绝分支中的失败计数/过期撤销有明确的提前提交边界，详见修复交接。
- `AuditRepository.add`：写审计事件；`AuditAction` 枚举需随阶段扩展。
- `AppShell`：新页面只需在 `router/navigation.ts` 增加条目并把 `readyPhase` 改为 `null`，再在 `router/index.ts` 注册 `withShell(...)`。
- `EmptyState` 的 `phaseNote`：页面从占位变为真实内容时移除该参数。
- `frontend/src/styles/tables.css`：新表格自动获得 44/56 规范尺寸。
- `frontend/src/composables/useServiceHealth.ts`：轮询就绪结果的连接状态，新页面如需展示连接健康可直接复用。

### 阶段 02 需要注意的已知问题

1. **`users.role` 的 check 约束只允许 `OWNER`。** 若阶段 02 需要员工账号或其它角色，必须新建迁移修改该约束；不要靠改动模型声明就当已生效（模型与迁移必须一致，就绪检查比对的是迁移版本）。
2. **`platform_state` 行必须保留。** 公司信息建议新建 `companies` 表并用外键关联，而不是把公司字段塞进 `platform_state`。
3. **公司暂停状态尚未存在。** PAGES.md 要求"暂停公司固定在顶部"。阶段 01 顶栏只显示服务连接状态，没有公司运行状态徽标——因为还没有暂停功能，显示"运行中"会是未测量的宣称。阶段 02 实现暂停后，在此处加入真实的公司状态徽标与暂停/恢复按钮。
4. **顶栏连接状态已是实测值，但仍是 30 秒轮询。** `composables/useServiceHealth.ts` 每 30 秒调用 `/health/ready`，标签页隐藏时停止，恢复可见时立即重查；显示"检查中/服务已连接/连接中断"并带最近检查时间。阶段 03 引入 SSE 后应改为推送，但不要把状态改回静态文本。
5. **`/api/v1/system/status` 只返回部署事实。** 若阶段 02 需要展示公司/组织概要，请新增独立端点，不要往这个响应里塞业务数据。
6. **Worker 仍是纯心跳循环。** 阶段 02 若需要后台作业，应新建独立 worker 模块，不要修改 `health_worker.py` 的职责边界。
7. **审计界面尚不存在，但事件已记录。** 阶段 02 可先提供只读审计列表；注意 `audit_events.detail` 是自由文本，展示时按脱敏内容处理。
8. **前端未引入路由级错误边界。** 新增页面若抛异常会显示空白。建议在阶段 02 加 `onErrorCaptured` 或路由级 `ErrorState`。

### 建议先处理的事项

1. 确认 `.env` 中的 `PUBLIC_BASE_URL` 与实际访问地址一致，否则所有写操作返回 403（这是设计行为，不是缺陷）。
2. 运行一次 `python scripts/verify_phase01.py` 确认基线未被破坏，再开始阶段 02。
3. 阶段 02 引入公司创建时，把"创建公司"与"创建任务/执行"放在同一事务模式里思考（批准/预算/任务/事件同事务），不要先写只创建记录的版本再补——架构文档要求这些保持一致。
4. 若阶段 02 新增 Element Plus 组件（如 Tree、Tabs、Radio），记得在 `main.ts` 注册，否则运行时静默不渲染。
