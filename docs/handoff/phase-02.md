# 阶段 02 交接：公司模板、组织编辑器与模型中心

日期：2026-10-30。验收报告：[phase-02.md](../acceptance/phase-02.md)。证据索引：[evidence/phase-02/README.md](../acceptance/evidence/phase-02/README.md)。

## 本阶段范围

已完成：
- 公司初始化向导（四步）与幂等、并发安全的一次性创建；一次生成 1 部门、5 岗位、5 员工。
- 组织编辑器：员工资料、岗位职责（带版本）、主管关系（服务端防环）、模型绑定、启停/归档；身份与全部履历只追加不删除。
- 模型中心：供应商、模型、员工分配三分离；DeepSeek 预设与自定义 OpenAI 兼容接口；密钥仅环境变量引用。
- 统一模型适配器（单次调用、硬超时、稳定错误码、JSON 输出校验）与独立的探测用量登记。
- 审计写入覆盖上述全部变更，审计界面只读。
- 概览页改为显示真实公司与在册员工数，不再只显示阶段 01 空壳文案。

明确不在本阶段范围：任务调度与预算（03）、隔离执行器（04）、招聘/调岗审批与员工账号（06）、知识库（07）。

## 工程入口

后端（`backend/`）：

| 文件 | 职责 |
|---|---|
| `app/models/organization.py` | 全部阶段 02 表定义与约束 |
| `alembic/versions/7b41c9d2f0aa_phase_02_company_organization_models.py` | 建表 + 模板种子（不创建公司行） |
| `app/domain/company_template.py` | 模板内容的 Python 来源；与迁移种子由测试强制一致 |
| `app/repositories/organization_repository.py` | 数据访问；含员工图遍历与行锁 |
| `app/services/organization_service.py` | 公司创建/更新、岗位版本、员工编辑与防环、模型绑定 |
| `app/services/model_centre_service.py` | 供应商/模型 CRUD、探测编排、用量与审计 |
| `app/providers/adapter.py` | 统一模型适配接口（唯一与供应商通信的地方） |
| `app/api/v1/organization.py`、`model_centre.py`、`audit.py` | 路由 |
| `app/api/schemas_organization.py` | 请求/响应模型（不含任何可携带密钥值的字段） |

前端（`frontend/src/`）：
- `views/CompanySetupView.vue`：向导；一次会话内复用同一 `idempotencyKey`。
- `views/OrganizationView.vue`：层级/列表、员工抽屉（资料/职责/主管/模型/状态/履历）、岗位抽屉。
- `views/ModelsView.vue`：三 tab；供应商抽屉只收环境变量名；“测试连接”是独立端点。
- `views/AuditView.vue`：只读列表 + 动作筛选 + 分页。
- `api/organization-types.ts`、`api/endpoints.ts`：新增类型与端点封装。
- `stores/session.ts`：新增 `companyInitialized`/`companyName`/`markCompanyInitialized`。
- `router/index.ts`：`/setup/company` 路由；守卫在 `companyInitialized === false` 时把已登录所有者送去向导。

实际可用命令（Windows、Git Bash）：

```powershell
# 后端
cd backend
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy app
$env:TEST_ADMIN_DATABASE_URL='postgresql+psycopg://acos:review-local-test-only@127.0.0.1:55433/postgres'
./.venv/Scripts/python.exe -m pytest -q

# 前端
cd frontend
pnpm check

# 隔离栈 + 端到端 + 浏览器
./scripts/phase02_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_ui.cjs
node scripts/verify_phase02_zoom200.cjs --method=equiv
node scripts/verify_phase02_zoom200.cjs --method=os-input   # 当前主机被阻塞
```

配置变量（只列名称，不含秘密值）：`DEEPSEEK_API_KEY`（Compose 已透传到 api 服务；留空即“未配置”）。其余沿用阶段 01。

## 变更

数据表（新增 12 张）：`companies`、`company_template_positions`、`departments`、`positions`、`position_versions`、`agents`、`agent_duty_override_history`、`providers`、`provider_models`、`agent_model_assignments`、`model_probes`、`provider_usage_records`。

API（新增）：
- `GET /api/v1/company/template`、`GET|POST|PATCH /api/v1/company`
- `GET /api/v1/organization`、`PATCH /api/v1/positions/{id}`、`PATCH /api/v1/agents/{id}`、`POST /api/v1/agents/{id}/model-assignment`、`GET /api/v1/agents/{id}/history`
- `GET /api/v1/models/centre`、`POST /api/v1/providers`、`PATCH /api/v1/providers/{id}`、`POST /api/v1/models`、`PATCH /api/v1/models/{id}`、`POST /api/v1/models/{id}/probe`、`GET /api/v1/models/{id}/probes`
- `GET /api/v1/audit/events`（只读）
- `GET /api/v1/auth/setup-status` 增加 `companyInitialized`、`companyName`

**兼容性**：`setup-status` 是唯一被扩展的既有响应，新增字段不破坏旧客户端。没有删除或改名任何阶段 01 字段与表。无数据回填需求（新表）。

**升级/回退**：`alembic upgrade head` 即可；实测 01→02→降级→再升级往返正常。降级会删除阶段 02 全部表与其数据，仅在没有公司数据时安全；生产环境回退前必须先备份。

**设计 token 变更（唯一一处）**：`color.disabledText` 由 `#64748B` 改为 `#475569`，原因见下文“关键决定”。`docs/design/design-tokens.json` 与 `UI-SPEC.md` 已同步，`pnpm tokens` 可重新生成。

## 关键决定

1. **单公司用 `id = 1` + 检查约束**，而不是唯一索引；迁移不插公司行，因此 `GET /company` 在向导完成前如实返回 404。见 [ADR-0003](../decisions/0003-phase-02-organization-and-model-centre.md)。
2. **职位版本与乐观锁分离**：`current_version` 只表示职责快照号，`row_version` 是每次编辑都递增的并发令牌。实施中发现若混用，两个“只改名”的客户端会静默互相覆盖。
3. **职责双轨**：岗位版本（对岗位的定义）与员工覆盖（对个人的追加）分开，覆盖变更写入 `agent_duty_override_history`，清除覆盖也有记录。这样“职责变更保留履历”可被查询验证，而不是口头承诺。
4. **绑定只关闭不删除**：`agent_model_assignments.ended_at` + 部分唯一索引保证每员工至多一个开放绑定，同时保留全部历史，供阶段 05 的执行记录回指。
5. **防环在服务端、在锁内**：`PATCH /agents/{id}` 先锁全公司员工行再验证，避免两个并发重连各自基于旧图通过校验。UI 侧过滤只是体验优化，服务端仍是唯一权威。
6. **保存与探测分离**：探测端点使用已存配置，绝不隐式保存；探测正好一次调用（`CHECK call_count <= 1`），异常路径也登记用量，未知用量记 `NULL` 而非 0。
7. **密钥只有引用**：schema 层面没有可存密钥的列；`secret_ref` 受正则约束为环境变量名；界面无密钥输入框（测试断言 `input[type=password]` 为 0）。
8. **禁用色加深**：实测禁用按钮文字仅 3.86:1，低于 4.5:1 门禁。选择加深文字而不是降低整体透明度，因为规范要求禁用控件保留形状与可读性。

## 验收

见[验收报告](../acceptance/phase-02.md)。摘要：
- 后端 141 passed（新增 76）、4 个既有弃用警告；ruff/format/mypy 全通过。
- 前端 83 passed（新增 9）；lint/typecheck/build 通过。
- 真实 Compose 栈 31 项端到端检查通过；浏览器 18 项检查通过，四视口 + 键盘 + 对比度实测。
- 200% **等效布局**门禁通过；**真实浏览器 200% 缩放未通过**（前台已建立但 OS 输入未到达渲染器，`zoom-200-real.json` 记录诊断）。
- 真实供应商调用 0 次；模拟四类场景（正常/错误密钥/超时/无效 JSON）全部通过。

## 下一阶段接入点

阶段 03（持久任务与预算）应复用而非重写：
- `app/providers/adapter.py`：统一调用入口。阶段 03 需要在其上增加重试策略（当前明确单次）、流式与输出上限，但**不要**在业务层直接调用 httpx。
- `provider_usage_records`：目前只承载 `source='PROBE'`。阶段 03 应把 `source` 扩展为执行用量并并入统一账本；`usage_known=false` 的语义（未知不是 0）必须保留。
- `agent_model_assignments`：执行记录应保存所用绑定 id，以实现“旧执行保持历史配置”。
- `position_versions` / `agent_duty_override_history`：执行快照应记录岗位版本号。
- 审计：`AuditAction` 枚举按需扩展；审计写入必须在与业务变更同一事务内，沿用 `AuditRepository`。
- 前端：`ModelCentreResponse` 的 `usage` 只是探测用量，不要当成总预算展示。

已知问题与建议先处理：
1. **真实浏览器 200% 缩放仍未验证**（阶段 01 遗留 + 本轮复现）。需在真实交互桌面完成，或改用能注入真实输入的控制环境；不要把 `--method=equiv` 记作通过。
2. 真实供应商连通性未验证。拿到密钥后应先用模型中心“测试连接”确认，再让阶段 03 依赖它。
3. 前端 `vue-tsc` 在模板属性值里遇到 `https://...` 字面量时会破坏插槽作用域推断（表现为 `Cannot find name 'controlId'`）。当前实现把示例 URL 放在脚本常量里传给 `:hint`。后续新增表单请沿用这一写法。
4. 组织编辑目前仅所有者可操作，无并发编辑冲突提示之外的协作语义；若阶段 06 引入员工主体，必须重新审视“管理关系不授予权限”的实现是否仍然成立。
