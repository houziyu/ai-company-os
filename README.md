# AI Company OS

当前实施到阶段 03；2026-11-02 完成持久任务调度与预算，后端 195 项、前端 103 项测试通过。最新结果见 [阶段 03 验收](docs/acceptance/phase-03.md) 与 [交接](docs/handoff/phase-03.md)。**阶段 02 的真实浏览器 200% 缩放、真实供应商连通性与真实进程强杀恢复仍未验证**；阶段 03 另有独立的操作系统级强杀恢复证据。

受控的 AI 研发公司运行平台：单所有者、单公司、多项目。当前**阶段 01、02、03 已实现**——账号体系、Compose 部署、公司初始化向导、五岗位五员工、组织编辑器、模型中心与统一适配接口，以及持久任务队列、执行记录、三层 Token 预算与任务看板。隔离执行器、五岗位交付闭环、提案审批与知识库属于后续阶段。

技术基线：FastAPI + Pydantic + SQLAlchemy 2 + Alembic、PostgreSQL 17、Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus、Docker Compose。

## 阶段 01 实际可用的功能

- 首次部署创建唯一所有者账号（只允许一次，并发请求由数据库行锁 + 主键保证）。
- 登录、注销、服务端会话、会话过期与撤销、登录失败次数限制。
- 会话 Cookie 为 HttpOnly + SameSite=Lax；写操作要求会话绑定 CSRF 头，并校验请求来源。
- 存活探针 `/api/v1/health/live`（不依赖数据库）与就绪探针 `/api/v1/health/ready`（数据库可用 + 迁移版本匹配）。
- 中文工作台外壳：九个一级导航，未开放页面显示明确的空状态与开放阶段，不提供假按钮。
- 顶栏连接状态为实测值：轮询就绪接口，未就绪或断网时显示"连接中断"。
- 设计 token 由 `docs/design/design-tokens.json` 生成 CSS 与 Element Plus 主题映射。
- Worker 维持心跳并暴露健康端点（阶段 01 行为保留为其基础职责）。

## 阶段 02 实际可用的功能

- **公司创建向导**（`/setup/company`，四步）：公司名与目标 → 科技公司模板（CEO、产品经理、技术负责人/开发、QA、DevOps）→ 模型配置或“稍后配置” → 确认创建。一次创建 1 个部门、5 个岗位、5 名独立员工；相同幂等键重复提交返回既有公司，不会复制；并发提交只有一次成功。
- **组织编辑器**（`/organization`）：层级/列表双视图；可编辑岗位名称与职责（职责变更追加版本、旧版本保留）、员工显示名、职责覆盖（含清除，均写履历）、主管关系、模型绑定、暂停/恢复/归档。
  - 服务端拒绝循环汇报关系（跨层环与自环均返回 409 并定位到 `managerId` 字段），管理层在锁内校验，并发重连不会各自通过。
  - 汇报关系**不授予任何资源权限**；员工不是登录主体，写操作仍只认所有者会话 + CSRF。
  - 身份与履历只追加：没有删除端点，归档保留工号、岗位、职责履历与模型绑定历史。
- **模型中心**（`/models`）：供应商、模型、员工分配三个分区（不会把 Agent ID 当模型 ID）。
  - 支持 DeepSeek 预设与自定义 OpenAI 兼容接口；Base URL 接受 `host`、`host/v1`、完整 `chat/completions` 三种写法。
  - **密钥在界面直接填写**：服务端用 AES-256-GCM 加密后保存（`providers.secret_ciphertext`，数据密钥在环境变量 `SECRET_ENCRYPTION_KEY`）。保存后不回显，列表只显示“已配置 / 未配置 / 无法解密”。旧的环境变量引用（`secret_ref`）仍兼容。
  - 保存配置与联网探测是两个独立命令：保存不触网，测试使用已保存的配置。
- **统一模型适配接口**（`backend/app/providers/adapter.py`）：单次调用、硬超时（1–120 秒）、稳定错误分类（认证失败/限流/超时/网络/HTTP/响应格式/JSON 无效）、JSON 输出校验。协议兼容**不**被当作能力已验证：`supportsJsonOutput` 在探测成功前为“未验证”。
- **探测用量独立登记**：每次探测最多一次调用（数据库约束 `call_count <= 1`），不自动重试；即使返回无效 JSON 也登记已消耗的 Token；供应商未返回用量时记录为“未知”，不记为 0。阶段 03 起探测同时受公司预算约束（见下）。
- **审计**（`/audit`）：公司、岗位、员工、状态、模型绑定、供应商、模型与探测结果均写入审计记录；审计界面只读、可筛选、服务端二次脱敏。

### 第二版复审修复（2026-11-02）

- 探测完成时按固定顺序锁定并刷新当前配置，迟到结果只写历史；模型中心、员工分配与组织页统一判断能力证据是否有效。
- 新增迁移 `8c24a71d9e03`，回填配置指纹、使无法归属的旧能力证据失效，保留探测、用量与审计历史。
- 总超时可取消正在接收响应头或响应体的网络请求；非法 Unicode 模型名被丢弃，合法用量仍可落库。
- 员工资料保存后立即应用服务端版本，刷新失败仍保留草稿；同时修复手机抽屉超出视口的问题。
- 详见[验收与迁移说明](docs/acceptance/phase-02-v2-fixes.md)及[四视口检查证据](docs/acceptance/evidence/phase-02-v2-fixes/README.md)。

### 独立质检修复（R1–R11，历史修复轮）

阶段 02 的独立质检发现 11 个问题，均已修复并有正式回归（详见[修复验收](docs/acceptance/phase-02-fixes.md)）：

- **探测不会泄漏或丢失证据**：供应商返回的模型标识在写库与返回前按当前凭据筛查（R1）；调用前先提交 `PENDING` 意图，中断留下“结果与用量未知”而非空白（R2）；畸形响应（超长模型名、超范围 Token、空 `choices`）不再触发 5xx 或回滚已发生的调用，且合法用量单独保留（R5）。
- **配置验证归属正确**：供应商与模型有乐观锁版本，过期表单返回 409（R7）；地址/类型/密钥引用/超时变更会作废旧的能力验证并保留历史，迟到结果不更新新配置（R3）。
- **用量与超时如实**：只报告一个维度时不再把另一个当作 0，汇总区分完整/部分/未计量（R4）；配置的超时是覆盖连接、读取、解析的总预算，并有响应体积上限（R6）。
- **界面行为可靠**：资料保存成功但改绑失败时提示已保存范围并继续可用（R8）；模型中心三个标签可用键盘到达（R10）；选择“未绑定”会真正解绑并保留历史（R11）；备注可清空（R9）。

## 阶段 03 实际可用的功能

- **项目与任务**（`/projects`）：创建项目（编号、目标、运行模式、Token 额度）；任务看板按业务状态分组，可切列表；手机默认列表。看板只渲染有任务的状态列，空状态在工具条以文字说明，避免空列把真实卡片挤出可视区。
- **任务详情**（`/tasks/:id`）：目标与验收、每次执行的独立记录（attempt、worker、fencing token、租约）、增量时间线、本次执行账本。取消是**协作式**的：排队任务立即取消，运行中的调用在安全检查点停止并记录停止时的用量；失败/阻塞/中断可重试，重试创建**新的执行 ID** 并重新预留额度，旧证据与账本保留。
- **执行详情**（`/executions/:id`）：步骤时间线（预估/输入/输出/耗时）与逐笔账本（预留、结算、释放）。
- **预算**（`/budgets`）：公司、项目两层额度的上限/已用/预留/可用四值；待核对用量单列；账本明细可按预留/结算/释放筛选。未配置价格时只显示 Token，不折算金额；不提供「充值」或在线改额度（无此命令，界面不承诺）。
- **持久队列与 Worker**：PostgreSQL 事务领取（`SKIP LOCKED`）、租约、心跳、单调 fencing token；旧租约的结果写入被拒绝。Worker 进程退出后，过期尝试标记 `INTERRUPTED`（`interruptedUnknownSideEffects=true`），只有声明幂等的处理器才自动重排，否则等人工作业。
- **真实故障证据**：两个真实 worker 进程竞争、操作系统级强杀（`taskkill /F`）、两个 worker 容器 + `docker kill`、整栈停机再启动，均验证了「中断 → 新执行 ID → 完成」。见[阶段 03 验收](docs/acceptance/phase-03.md)。
- **事件与 SSE**：`domain_events` 自增 ID；`Last-Event-ID` 或 `?after=` 严格回放其后事件；浏览器在事件流被中断时显示「连接中断，结果可能未更新」并自行退避重连（非 2xx 是 EventSource 的致命错误，浏览器不会自动重试）。
- **公司暂停**：顶栏固定入口。暂停后不再发起新的模型调用、不再创建新任务、Worker 不再领取；正在进行的调用按检查点停止或收尾。恢复后可继续。
- **确定性演练处理器**：所有任务执行都是演练（`SUCCESS`/`TRANSIENT_FAILURE`/`PERMANENT_FAILURE`/`SLOW`/`UNKNOWN_USAGE`/`TIMEOUT`），页面明确标注「演练运行（确定性处理器，非真实模型输出）」。`REAL` 运行模式在服务端被拒绝（409 `RUN_MODE_UNAVAILABLE`），**不会**静默降级。
- **未知用量**：一律按预留额度保守计入已用并标记「待核对」，`usage_tokens` 保持 `NULL`，不写 0。结算与预留在数据库层有唯一索引防重复。

### 升级注意（阶段 03 迁移）

新迁移 `c41f7b93e2a5` 为每个既有公司补建一个**额度 0** 的公司预算账户。升级后需显式设置额度，否则新任务会被拒绝（`BUDGET_INSUFFICIENT`）——这是刻意的失败，不是 bug。降级到 `8c24a71d9e03` 会删除阶段 03 全部表并把暂停状态复位，只在无任务数据时安全。详见[阶段 03 验收](docs/acceptance/phase-03.md)。

明确**未**实现：隔离执行器与工具网关、五岗位交付闭环、提案与审批、知识库、招聘与调岗审批、员工账号、真实模型执行。这些属于阶段 04—08。

## 快速启动

前置条件：Docker 与 Docker Compose v2+。

```bash
# 1. 生成 .env（随机数据库密码，拒绝覆盖已存在的文件）
python scripts/generate_secrets.py

# 2. 构建并启动
docker compose up -d

# 3. 等待全部健康
docker compose ps

# 4. 打开 http://localhost:8080 ：首次进入自动跳到 /setup 创建所有者账号，
#    之后自动进入 /setup/company 创建公司（五岗位五员工）
```

`python scripts/generate_secrets.py` 在 Windows / macOS / Linux 均可用（Python 是本地后端开发的既有前提）。需要自定义端口、绑定地址或对外地址时：

```bash
python scripts/generate_secrets.py --port 8443 --bind 0.0.0.0 \
  --public-url https://acos.example.com --secure-cookie
```

`PUBLIC_BASE_URL` 同时是写请求来源白名单的锚点，必须与浏览器地址栏完全一致（协议、主机、端口）。不一致会导致所有写操作返回 `403 ORIGIN_NOT_ALLOWED`。

第一个访问者必须立即完成初始化。初始化完成前，任何人打开站点都会被引导到 `/setup`；因此首次启动后请尽快完成，或先只监听回环地址（默认行为）。

## 检查命令

以下命令均已在阶段 01 实际执行，结果见 [docs/acceptance/phase-01.md](docs/acceptance/phase-01.md)。

### 后端

```bash
cd backend
python -m venv .venv                      # 首次
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# .venv/bin/python -m pip install -r requirements-dev.txt          # macOS / Linux

# 静态检查
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app

# 测试（需要可用的 PostgreSQL，见下）
./.venv/Scripts/python.exe -m pytest -q
```

测试使用真实 PostgreSQL，每个测试克隆一个由迁移生成的模板数据库，不降级到 SQLite。默认连接串可用 `TEST_ADMIN_DATABASE_URL` 覆盖，默认值为本地 `postgresql+psycopg://acos:devpass@127.0.0.1:55432/postgres`。启动一个供测试使用的临时数据库：

```bash
docker run -d --name acos-pg-dev -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_USER=acos -e POSTGRES_DB=acos -p 55432:5432 postgres:17-alpine
```

### 前端

```bash
cd frontend
pnpm install
pnpm tokens        # 由 design-tokens.json 生成 CSS / 主题 / TS 常量
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

### 端到端验收（对已启动的 Compose 栈）

```bash
python scripts/verify_phase01.py --base-url http://127.0.0.1:8080
```

脚本执行健康检查、首次初始化幂等、登录/注销、会话撤销后重放拒绝、CSRF 与跨站写拒绝、未认证访问拒绝、SPA 与安全响应头等检查；数据库端口通过本机 Docker 实时检查（`--compose-project` 指定对应项目，默认 `ai-company-os`）。缺 Docker 或未找到运行中的 postgres 时该项失败，不计作通过。全新栈为 21 项，已有账号时初始化专项不执行；注意：脚本会在尚无所有者时创建账号，只对全新或一次性栈运行。

本轮提供隔离质检栈入口（PowerShell，从根目录运行），使用独立镜像标签、项目、端口 `18081` 和卷 `acos-phase01-fixes_postgres_data`，不加载或覆盖 `.env`：

```powershell
./scripts/phase01_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase01.py --base-url http://127.0.0.1:18081 --compose-project acos-phase01-fixes
./scripts/phase01_test_stack.ps1 -ComposeArgs @('stop')
```

此入口里的固定凭据只供隔离测试；不能用于真实部署。不删除测试或现有数据卷。浏览器回归入口为 `scripts/verify_phase01_ui.cjs`（需要 `NODE_PATH` 指向含 Playwright 的依赖目录及已安装的 Chrome）；它固定访问上述隔离端口，截图与结果写入修复证据目录。

阶段 02 提供独立入口（端口 `18083`、项目 `acos-phase02-test`、独立卷），不加载 `.env`，`DEEPSEEK_API_KEY` 显式留空以复现“未配置”状态：

```powershell
./scripts/phase02_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_ui.cjs
node scripts/verify_phase02_zoom200.cjs --method=equiv
./scripts/phase02_test_stack.ps1 -ComposeArgs @('stop')
```

`verify_phase02.py` 只在全新栈上运行（会创建所有者）；重跑前需删除该项目的数据卷。`verify_phase02_zoom200.cjs --method=equiv` 是 200% 缩放的**等效布局**门禁；`--method=os-input` 是真实浏览器缩放尝试，在当前主机被输入通道问题阻塞，详见 [阶段 02 验收](docs/acceptance/phase-02.md)。

阶段 03 提供隔离入口（端口 `18085`、项目 `acos-phase03-test`、独立卷，两个 worker 服务）：

```powershell
./scripts/phase03_test_stack.ps1 -ComposeArgs @('up','-d','--build','--scale','worker=1','--scale','worker2=1')
./backend/.venv/Scripts/python.exe scripts/verify_phase03_compose.py
./backend/.venv/Scripts/python.exe scripts/verify_phase03_restart.py
./scripts/phase03_test_stack.ps1 -ComposeArgs @('stop')   # 保留容器与卷
```

不依赖 Compose 的证据脚本：`scripts/verify_phase03_migration.py`（迁移往返，自建临时库）、`scripts/verify_phase03_runtime.py`（真实 API + 两个真实 worker 进程 + 操作系统级强杀，需 `PHASE03_DATABASE_URL` 指向隔离库）、`scripts/verify_phase03_ui.cjs`（真实构建 SPA + 真实 API 的四视口截图与键盘检查，需 `PHASE03_UI_BASE_URL`）。`phase03_test_stack.ps1 stop` 不会删除数据卷。

## 配置与秘密

复制 `.env.example` 为 `.env` 并替换占位值，或使用 `scripts/generate_secrets.py` 生成随机值。`.env` 已在 `.gitignore` 中，不得提交。

| 变量 | 说明 |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | 数据库账号与库名 |
| `DATABASE_URL` | SQLAlchemy 连接串，密码需 URL 编码；必须与上面三项一致 |
| `PUBLIC_BASE_URL` | 浏览器访问地址，也是写请求来源白名单锚点 |
| `EXTRA_ALLOWED_ORIGINS` | 额外允许来源，JSON 数组，例如 `["http://192.168.1.20:8080"]` |
| `SESSION_TTL_HOURS` | 会话有效期（默认 12 小时） |
| `COOKIE_SECURE` | 通过 HTTPS 访问时必须设为 `true` |
| `PASSWORD_MIN_LENGTH` | 密码最小长度（默认 12，下限 12） |
| `LOCKOUT_THRESHOLD` / `LOCKOUT_MINUTES` | 窗口内失败次数上限与锁定时长 |
| `WEB_BIND_ADDRESS` / `WEB_PORT` | 发布端口，默认只监听 `127.0.0.1:8080` |
| `ENABLE_API_DOCS` | 是否暴露 `/api/docs`，默认关闭 |
| `SECRET_ENCRYPTION_KEY` | 供应商密钥的加密主密钥（32 字节随机值的 base64）。缺失时模型中心拒绝保存密钥，不会明文入库 |
| `DEEPSEEK_API_KEY` | 旧式来源：以环境变量名引用密钥时的**值**，只存在于 api 进程环境。直接在模型中心填写密钥的新配置无需填写 |

### 数据库迁移与备份

迁移历史（当前 head `a7d2e5c81b64`）：`6a790d8c2b88`（阶段 01）、`7b41c9d2f0aa`（公司/组织/模型表）、`3f8d2c6a91b4`（配置版本与指纹、`PENDING` 探测、按维度用量）、`8c24a71d9e03`（旧指纹与能力证据修复）、`c41f7b93e2a5`（阶段 03 任务、执行、事件、消息、预算）、`e5b7c9d1a2f4` 与 `f6c8d0e2b4a7`（事件发布游标与兼容重放）、`a7d2e5c81b64`（供应商密钥加密存储，ADR-0006）。`8c24a71d9e03` 是在线数据迁移，不删除历史；`c41f7b93e2a5` 为既有公司补建额度 0 的公司预算账户，升级后需显式设置额度。升级前先备份数据库并记录当前版本，升级后验证迁移版本与就绪接口。完整命令、降级边界与验证方式见[阶段 03 验收](docs/acceptance/phase-03.md)与[密钥存储验收](docs/acceptance/provider-secret-storage.md)。`a7d2e5c81b64` 的降级在仍有加密密钥时会拒绝执行，需先清除这些密钥。

密钥处理约定：

- 密码只以 Argon2id 哈希入库；会话令牌仅存 SHA-256 哈希，Cookie 明文不落库、不写日志。
- 日志为 JSON 结构化输出，事件文本和 extra 字段均使用白名单（请求 ID、方法、路径、状态、耗时等）；不记录请求体、Cookie、Authorization 头或密码。INFO/DEBUG 下 SQLAlchemy 均维持 WARNING，数据库引擎隐藏参数；异常只记录类型与栈位置，不记录异常原文、局部变量或源码行。
- `GET /api/v1/system/status` 只返回非敏感的部署事实（版本、迁移版本、Worker 心跳时间），有测试断言其响应不含密码、连接串或 `secret` 字样。
- 浏览器只收到会话与 CSRF 两个 Cookie；数据库连接串从不进入前端。
- **模型供应商密钥**：界面直接填写的密钥由服务端用 AES-256-GCM 加密后存入 `providers.secret_ciphertext`，主密钥来自环境变量 `SECRET_ENCRYPTION_KEY`，永远不在数据库中。响应只返回来源（`ENV`/`DB`）与状态（`NONE`/`CONFIGURED`/`MISSING_ENV`/`UNDECRYPTABLE`），不返回密钥、长度或任何派生值；审计只记录来源与是否配置。服务端缺失主密钥时拒绝保存（不会明文写入）；主密钥更换后旧密文无法解密，探测在本地拒绝且不产生费用。旧式 `providers.secret_ref` 环境变量名继续可用。探测失败时供应商响应正文（可能回显密钥）被丢弃，只保留稳定错误码。有测试用 canary 值扫描全部 API 响应、全部表的所有列与日志。
- **备份含义变化**：数据库备份现在包含**密文**。只有备份与 `SECRET_ENCRYPTION_KEY` 同时泄露才构成密钥泄露，请分别保管两者。
- 探测用量自阶段 03 起纳入公司预算：探测在提交 `PENDING` 意图的同一短事务内持一笔公司预留，结束后按上报用量结算；公司暂停或额度不足时在本地拒绝且不发起请求。建公司前的探测不占额度（此时没有公司），响应会如实说明。

**轮换数据库密码**：`generate_secrets.py --force` 会生成新密码，但已存在的数据库卷里仍是旧密码，直接重启会使 API 无法连接。正确步骤是先用旧凭据登录数据库执行 `ALTER USER acos WITH PASSWORD '...'`，再更新 `.env` 并重启。

**更换 `SECRET_ENCRYPTION_KEY`**：已保存的供应商密钥无法用新主密钥解密，模型中心会显示“无法解密”，探测在本地拒绝并发进行。系统不会批量重加密历史密文；更换后请在每个供应商上重新填写密钥。不要删除或丢失该值，它不在数据库备份中。

## 数据卷与持久化

- `ai-company-os_postgres_data` 保存全部业务数据。`docker compose down` 保留该卷，`docker compose down -v` 会**删除**它。
- **不要把 `down -v` 当作升级或修复手段。** 升级只需 `docker compose up -d --build`。
- 容器重建后数据保留（已实测）；仅重启容器同样保留（已实测）。

备份（阶段 01 未提供在线按钮，以下为手工命令）：

```bash
# 备份：数据库导出 + 版本信息
docker compose exec -T postgres pg_dump -U acos -d acos --format=custom \
  > backup-$(date -u +%Y%m%dT%H%M%SZ).dump
docker compose exec -T postgres psql -U acos -d acos -tc "SELECT version_num FROM alembic_version;"

# 恢复：先停 api/worker，再导入
docker compose stop api worker
docker compose exec -T postgres pg_restore -U acos -d acos --clean --if-exists < backup-XXXX.dump
docker compose start api worker
```

产物卷与一致性备份属于阶段 08 范围；当前阶段没有需要备份的产物。

## 部署边界

- 默认仅 `web` 发布端口，且绑定回环地址；`postgres`、`api`、`worker` 只在 Compose 内部网络可达，数据库端口未发布（已实测）。
- 容器共享宿主内核。对公网暴露时必须在前面加 TLS 终止代理，并把 `PUBLIC_BASE_URL` 改为 https 地址、`COOKIE_SECURE=true`。
- `web` 容器内 nginx 以非 root 用户运行，负责静态资源、SPA 回退与 `/api` 反向代理。
- API 或 Worker 都不获得宿主 Docker socket；阶段 01 根本没有执行器服务。

## 目录

```
backend/
  app/api/          HTTP 层：路由、依赖、中间件、统一错误
  app/domain/       枚举、错误、密码与令牌原语、公司模板定义
  app/services/     应用服务：认证、健康检查、组织、模型中心
  app/providers/    统一模型适配接口（唯一与供应商通信的地方）
  app/repositories/ 数据访问
  app/models/       SQLAlchemy 表定义（entities.py 阶段 01，organization.py 阶段 02，tasks.py 阶段 03）
  app/workers/      阶段 03 任务消费者（task_worker）与确定性演练处理器（demo_processor）
  alembic/          迁移脚本
  tests/            真实 PostgreSQL 集成测试
frontend/
  src/api/          统一 API 客户端（CSRF、错误映射）与端点封装
  src/components/   公共组件：AppButton、StatusBadge、FormField、EmptyState、ErrorState、ConfirmDialog、AppDrawer、PageHeader
  src/layouts/      AppShell、AuthLayout
  src/router/       路由与导航元数据
  src/stores/       会话 store（含公司初始化状态）
  src/styles/       由 token 生成的主题 + 基础样式
  src/views/        登录/初始化、公司向导、概览、组织、模型中心、审计等
  scripts/          token 生成器
deploy/nginx/       非 root nginx 配置与安全响应头
scripts/            秘密生成、阶段验收、浏览器与缩放门禁脚本
docs/               规范、验收与交接记录
```

## 已知限制

- 提案与知识两个页面仍是带说明的空状态；概览、组织、模型中心、项目与任务、预算、审计已接入真实功能。
- Worker 只运行**确定性演练处理器**：不调用真实模型、不执行生成代码、不接触 Docker socket。真实执行属阶段 04/05。
- 真实运行模式（`REAL`）在服务端被拒绝，不会被静默降级为演练。
- 预算账户余额是缓存值，账本是事实来源；目前没有自动对账任务。
- 既有公司升级后公司额度为 0，需运营显式设置，否则新任务被拒绝。
- 真实浏览器 200% 缩放仍未验证：本机自动化 Chrome 虽已置为前台，但 OS 注入的按键没有到达其渲染器（页面记录 0 个键盘事件）。等效布局门禁已通过，但不能替代真实缩放。
- 探测的中断行为用注入异常验证（`PENDING` 意图不随请求事务回滚），未做操作系统级进程强杀。
- 真实 DeepSeek 连通性未验证（无密钥）。协议模拟覆盖了正常 JSON、错误密钥、超时、无效 JSON 四类场景。
- 模型能力只按探测结果标注：`supportsJsonOutput` 在成功前为“未验证”，不因供应商声称兼容而置为支持。
- 没有暗色主题、多语言、多租户；这些都是基线之外的范围。
- 未在真实 HTTPS 域名下验证 `Secure` Cookie 行为（见验收报告的未验证项）。

## 文档

- 工程现状与阅读顺序：[docs/START-HERE.md](docs/START-HERE.md)
- 架构约束：[docs/architecture.md](docs/architecture.md)
- 实现规范：[docs/engineering-standards.md](docs/engineering-standards.md)
- 设计规范：[docs/design/](docs/design/)
- 阶段 01 验收：[docs/acceptance/phase-01.md](docs/acceptance/phase-01.md)
- 阶段 01 交接：[docs/handoff/phase-01.md](docs/handoff/phase-01.md)
- 阶段 03 验收：[docs/acceptance/phase-03.md](docs/acceptance/phase-03.md)（含[证据索引](docs/acceptance/evidence/phase-03/README.md)）
- 阶段 03 交接：[docs/handoff/phase-03.md](docs/handoff/phase-03.md)
- ADR-0004 任务调度与预算：[docs/decisions/0004-phase-03-task-scheduling-and-budget.md](docs/decisions/0004-phase-03-task-scheduling-and-budget.md)
- 阶段 02 验收：[docs/acceptance/phase-02.md](docs/acceptance/phase-02.md)（含[证据索引](docs/acceptance/evidence/phase-02/README.md)）
- 阶段 02 交接：[docs/handoff/phase-02.md](docs/handoff/phase-02.md)
- 阶段 02 独立质检（原始，未修改）：[docs/acceptance/phase-02-review.md](docs/acceptance/phase-02-review.md)
- 阶段 02 质检修复验收：[docs/acceptance/phase-02-fixes.md](docs/acceptance/phase-02-fixes.md)（含[证据索引](docs/acceptance/evidence/phase-02-fixes/README.md)）
- 阶段 02 质检修复交接：[docs/handoff/phase-02-fixes.md](docs/handoff/phase-02-fixes.md)
- 阶段 02 第二版复审：[复审报告](docs/acceptance/phase-02-recheck.md)
- 阶段 02 第二版修复：[验收](docs/acceptance/phase-02-v2-fixes.md)、[交接](docs/handoff/phase-02-v2-fixes.md)、[证据](docs/acceptance/evidence/phase-02-v2-fixes/README.md)
- ADR-0003 组织与模型中心决策：[docs/decisions/0003-phase-02-organization-and-model-centre.md](docs/decisions/0003-phase-02-organization-and-model-centre.md)
