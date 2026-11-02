# 阶段 02 验收报告：公司模板、组织编辑器与模型中心

日期：2026-10-30（Asia/Shanghai）
对应阶段：02
代码版本/分支：当前工作区无 `.git`，无法提供提交哈希或 Git 差异。
环境：Windows 10.0.26100、Python 3.13.12、Node 24.21.0、Chrome 156.0.8078.48、Docker 29.6.2、独立 PostgreSQL 17（测试库 127.0.0.1:55433，隔离 Compose 栈 127.0.0.1:18083）。依赖版本未升级。
本报告结论：**部分完成。功能、持久化、防环、身份历史、权限与密钥约束均实测通过；真实供应商连通性与真实浏览器 200% 缩放未验证，见“未通过与未执行”。**

## 前置条件核对

本轮先复核阶段 01 状态，再开始阶段 02：

| 前置项 | 核对方式与实际结果 |
|---|---|
| 阶段 01 后端回归 | 独立测试库上 `pytest` **65 passed / 4 warnings**（阶段 01 原始数量），与 R7 交接记录一致 |
| 阶段 01 前端回归 | `pnpm check` 通过，**74 tests**（含 R7 的 19 项会话时序用例） |
| R7 会话竞争修复 | 代码在位：`api/auth-failure.ts` 会话代次、`api/client.ts` 捕获代次、`stores/session.ts` 认证操作序号；`session-race.spec.ts` 19 项通过。未重写 |
| 实际 200% 缩放 | 阶段 01 仍未验证。本轮实机重试（见“UI 检查”）仍被同一类输入隔离问题阻塞，本轮补充了确定性诊断与等效布局门禁 |

阶段 01 已有的登录、会话、CSRF、迁移与部署机制全部保留，没有重建。

## 需求与证据

| 需求/验收 ID | 实际实现 | 测试方法与命令 | 实际结果 | 证据 |
|---|---|---|---|---|
| 02-1 一次初始化只生成五岗位五实例 | `CompanyService.initialize` 从 `company_template_positions` 实例化 1 个部门、5 个岗位、5 条岗位版本、5 个员工；员工按模板顺序创建以建立管理边 | `pytest tests/test_organization.py::test_company_creation_makes_exactly_five_positions_and_five_agents`；`verify_phase02.py` 公司创建项 | 通过：companies=1、positions=5、agents=5、position_versions=5、departments=1；5 个不同 id 与工号 | `backend-pytest.txt`、`e2e-compose.txt` |
| 02-2 重复提交不复制公司 | 幂等键 + 请求指纹存于 `companies`；相同键同内容返回 200 既有公司，不同请求返回 409 | `test_company_creation_is_idempotent_for_same_key`、`test_company_creation_rejects_different_payload_after_exists`；端到端“相同幂等键重复提交”“不同请求重复创建被拒绝” | 通过：201 → 200 同 id；409 `COMPANY_ALREADY_EXISTS`；库中始终 1 家公司、5 员工 | 同上 |
| 02-3 并发初始化只成功一次 | 公司在 `platform_state` 单例行锁内创建；`id = 1` + 检查约束为最终兜底 | `test_concurrent_company_creation_creates_exactly_one`（4 线程、各自独立 engine/app、屏障同步） | 通过：1×201 + 3×409；审计 `COMPANY_INITIALIZED` 仅 1 条 | `backend-pytest.txt` |
| 02-4 八个实体明确建模 | `companies/departments/positions/position_versions/agents/providers/provider_models/agent_model_assignments`，另有 `company_template_positions/agent_duty_override_history/model_probes/provider_usage_records` | 迁移 `7b41c9d2f0aa`；`alembic` 自动生成比较 diff=0 | 通过：19 张表，模型与迁移零差异 | `migration-path.txt` |
| 02-5 组织展示与编辑 | `/api/v1/organization`；`PATCH /positions/{id}`、`PATCH /agents/{id}`、`POST /agents/{id}/model-assignment`；页面层级/列表双视图 + 560 抽屉 | 端到端“合法汇报调整生效”“岗位职责编辑生成新版本”“员工改名刷新后仍在”；`phase02-views.spec.ts` | 通过：改名重载后仍为“质量工程师（改名）”；职责 v1→v2 且旧文本保留 | `e2e-compose.txt`、`ui-checks.txt`、`frontend-checks.txt` |
| 02-6 服务端拒绝循环汇报 | `AgentRepository.descendants` 在全公司员工行锁内计算；`CHECK (manager_id <> id)` 兜底自环 | `test_reporting_cycle_is_rejected_by_server`；端到端循环项；UI“级联主管选项被过滤” | 通过：CEO→QA、TECH_LEAD→QA 均 409 `REPORTING_CYCLE`，字段错误定位 `managerId`；自报 409 `REPORTING_SELF`；拒绝后树未变 | `backend-pytest.txt`、`e2e-compose.txt`、`ui-checks.txt` |
| 02-7 汇报关系不授予资源权限 | 无任何授权代码读取 `manager_id`；员工不是主体，写操作一律要求所有者会话 + CSRF | `test_management_relation_does_not_grant_resource_permissions`、`test_company_requires_authentication`、`test_company_write_requires_csrf`；端到端未认证/无 CSRF 项 | 通过：匿名 PATCH 401；无 CSRF 403 `CSRF_VALIDATION_FAILED`；`users` 仅 1 行 `OWNER`，管理边仅存在于 `agents.manager_id` | `backend-pytest.txt`、`e2e-compose.txt` |
| 02-8 职责与实例覆盖分离并记录版本 | `position_versions` 追加式；员工覆盖存 `agents.duty_override` 并写入 `agent_duty_override_history` | `test_position_duty_edit_persists_and_versions`、`test_duty_override_clearing_keeps_history` | 通过：版本 [1,2]、旧职责原文保留；覆盖与清除各留一条履历 | `backend-pytest.txt` |
| 02-9 换模型/暂停不删身份与履历 | 绑定关闭即写 `ended_at`，旧行保留；状态改为 PAUSED/ARCHIVED，`archived_at` 记录时间 | `test_agent_identity_and_history_survive_status_and_model_changes`、`test_model_assignment_records_history_and_closes_previous` | 通过：归档后工号/岗位/覆盖不变；绑定表 1 开 2 总；履历含旧绑定“（已结束）” | `backend-pytest.txt` |
| 02-10 供应商/模型/分配分离 | 三个 tab 与三组端点；`AgentModelBinding` 明确区分 Agent 与模型 ID | `phase02-views.spec.ts` 模型与分配 tab 用例；`ui-checks.txt` | 通过：员工分配表 5 行、绑定后显示具体模型；页面无 Agent/模型混用字段 | `frontend-checks.txt`、`ui-checks.txt` |
| 02-11 DeepSeek 预设 + 自定义兼容接口 | `deepseek_preset` 返回地址/环境变量名/建议模型；`kind` 支持 `DEEPSEEK`/`OPENAI_COMPATIBLE`；Base URL 三种写法归一 | `test_base_url_normalization`（5 组）、UI“填入 DeepSeek 预设” | 通过：`https://api.deepseek.com` → `/v1/chat/completions`；预设仅填名称不填密钥 | `backend-pytest.txt`、`ui-checks.txt` |
| 02-12 统一适配接口：超时/结构化错误/JSON 校验 | `app/providers/adapter.py`：单次调用、硬超时（1–120s 夹取）、稳定错误码、JSON 对象解析 | `tests/test_provider_adapter.py`（44 项）：超时夹取、状态码映射、畸形信封、非 JSON 正文、JSON 严格解析 | 通过：401/403→`AUTH_FAILED`、429→`RATE_LIMITED`、超时→`TIMEOUT`、连接失败→`NETWORK_ERROR`、非 JSON→`INVALID_RESPONSE` | `backend-pytest.txt` |
| 02-13 协议兼容不等于能力已验证 | `supports_json_output` 初始为 `null`；仅探测解析成功才置 `true`，失败置 `false` | `test_provider_and_model_crud_with_secret_status`、`test_probe_success_verifies_json_and_records_usage`、`test_probe_invalid_json_output_is_reported_and_usage_kept` | 通过：创建后 `null`；正常 JSON → `true`；无效 JSON → `false`，UI 显示“未验证/未通过” | `backend-pytest.txt`、`ui-checks.txt` |
| 02-14 密钥仅环境变量引用，界面只显示状态 | 仅存 `secret_ref`（变量名，正则约束）；`secret_configured` 由进程环境实时计算；无任何读密钥端点或密码输入框 | `test_no_secret_value_in_responses_database_or_logs`（遍历全部响应、所有表的所有列）、UI 断言 `input[type=password]` 数为 0 | 通过：canary 密钥未出现在 5 个响应、19 张表的任一行；`providers.secret_ref` 仅 `DEEPSEEK_API_KEY` | `backend-pytest.txt`、`ui-checks.txt` |
| 02-15 保存配置与联网探测分离 | 写配置端点不触网；`POST /models/{id}/probe` 只用已存配置 | `test_probe_does_not_save_configuration` | 通过：探测后 `logicalName/label/status/maxOutputTokens/providerId` 全未变；`sum(call_count)=1` | `backend-pytest.txt` |
| 02-16 一次探测最多一次调用、提示耗 Token、独立用量登记 | 每次探测建一行 `model_probes`；`CHECK call_count <= 1`；成功/失败各自登记 `provider_usage_records`；未知用量留 `NULL` 而非 0 | `test_probe_success_...`、`test_probe_invalid_json_output_...`（记录 21 输入 token）、`test_probe_without_secret_makes_no_call`、`test_missing_usage_is_unknown_not_zero` | 通过：`call_count` 恒 ≤1；无效 JSON 仍登记用量；缺密钥时 0 次调用且不造用量行 | `backend-pytest.txt` |
| 02-17 变更写审计、审计不可改 | 公司/岗位/员工/状态/绑定/供应商/模型/探测全部写 `audit_events`；审计路由只读 | `test_audit_records_organization_and_model_changes`、`test_audit_log_has_no_write_endpoint`；端到端审计项 | 通过：9 种动作齐备；DELETE/PATCH 审计返回 404 | `backend-pytest.txt`、`e2e-compose.txt` |
| 02-18 模拟供应商四类场景 | 通过 `httpx.MockTransport` 注入，不触网 | `test_probe_success_verifies_json_and_records_usage`（正常 JSON）、`test_probe_wrong_key_reports_auth_failure_without_leaking_body`（错误密钥）、`test_probe_timeout_is_a_distinct_error`（超时）、`test_probe_invalid_json_output_is_reported_and_usage_kept`（无效 JSON） | 通过：四类均返回稳定错误码；供应商原文（含被回显的密钥）不出现在响应、模型中心与审计中 | `backend-pytest.txt` |
| 02-19 密钥缺失明确显示未配置，不显示连接成功 | `secretConfigured=false`；探测被本地拒绝为 `MISSING_SECRET` 且 `callCount=0` | 端到端“密钥未配置时状态为 false”“缺少密钥的探测被本地拒绝”；UI 截图 | 通过：模型中心显示“未配置”，从未出现“已连接” | `e2e-compose.txt`、`models-providers-1440x900.png` |

后端 pytest 分布（共 **141** 项）：`test_organization.py` 32、`test_provider_adapter.py` 44、阶段 01 原有 65。前端 9 个文件共 **83** 项（新增 `phase02-views.spec.ts` 9 项）。

## UI 检查

真实栈（隔离 Compose，真实 API 与 PostgreSQL）上执行 `scripts/verify_phase02_ui.cjs`：**18 项检查通过，0 个页面异常**，截图见[证据目录](evidence/phase-02/README.md)。

- 视口：1440×900、1024×768、390×844、360×800 全部截图；四视口 `scrollWidth <= innerWidth`，无整页横向溢出。
- 状态：向导四步、组织层级/列表/抽屉/改名后、模型中心空态/供应商/模型/分配、审计列表与筛选、注销确认弹窗、移动端导航抽屉。截图均等待加载完成后再拍，避免骨架屏被误当成品。
- 尺寸：实测可见 `.app-button` 主高 32/40/48，移动端 ≥44。
- 对比度：由 `getComputedStyle` 实测（不是像素取样）；透明底按钮沿 DOM 取最近不透明祖先背景。本轮发现并修复一处真实缺陷：禁用态文字 `#64748B` 在 `#E2E8F0` 上仅 **3.86:1**，已改为 `#475569`（**6.15:1**）并同步 `design-tokens.json` 与 UI-SPEC。
- 键盘：员工行获得可见焦点环后可 Enter 打开抽屉；Escape 关闭抽屉；注销弹窗 Escape 关闭且焦点返回触发按钮；向导与登录表单路径可键盘完成。
- 循环关系：CEO 抽屉的主管下拉只剩“不向任何人汇报”——UI 与服务端同向拒绝，界面不提供必然失败的选项；服务端拒绝另有 API 与组件层双重证据。
- 持久化：界面改名后刷新仍生效；把 QA 改挂 CEO 后刷新，层级缩进变为 1，证明写入数据库而非本地状态。

### 200% 缩放

- **等效布局门禁：通过。** `scripts/verify_phase02_zoom200.cjs --method=equiv` 用 CDP 设备指标把 CSS 视口从 1440×900 减半为 720×450、DPR 由 1 翻为 2（这正是 200% 页面缩放产生的布局几何），在概览/组织/模型/审计/向导五个页面与员工抽屉上实测：无横向溢出、最小可见字号 12px、抽屉 560×450 完全落在视口内。证据：`zoom-200-equivalent.json`。
- **真实浏览器缩放：未通过（阻塞）。** `--method=os-input` 启动真实有头 Chrome 窗口并用 `SendInput` 发送 6 次 Ctrl+“=”。实测该自动化窗口**已**成为前台（`foregroundMatch=True`，窗口标题匹配），但页面内注册的 `keydown` 监听器记录到 **0** 个事件，DPR 保持 1、窗口宽度不变，因此浏览器从未收到缩放快捷键。结论：本机的进程间输入在前台窗口建立之后仍无法到达该 Chrome 的输入队列。证据：`zoom-200-real.json`（含 `foregroundAchieved`、`rendererReceivedKeyEvents: []`、`conclusion`）。
- 明确**不**把等效门禁当作真实缩放通过；`zoom-200-equivalent.json` 内写有 `explicitly_not` 字段。阶段 01 记录的同一门禁仍未关闭。

## 数据与安全

- **迁移**：新增 `7b41c9d2f0aa`。实测 01→02 表数 7→19，降级回 7 且无残留表，再升级回 19；模板 5 行种子正确；迁移**不**创建公司行（`companies` 为 0）。证据 `migration-path.txt`。破坏性变更：无；`users.role` 约束未改，未引入员工账号。
- **幂等与并发**：见 02-2、02-3。另有乐观锁 `row_version`（岗位）与 `version`（公司/员工），陈旧写入返回 409 `CONCURRENT_UPDATE`（`test_position_stale_version_is_rejected`、`test_company_update_bumps_version_and_audits`）。实施中发现：仅改岗位名称不追加职责版本，若用 `current_version` 做乐观锁会让两个“只改名”的客户端互相覆盖；因此新增独立的 `row_version`。
- **权限**：全部业务端点要求所有者会话；写端点另需 CSRF 头（端到端实测 403 `CSRF_VALIDATION_FAILED`）。对象归属逐条校验（`_require_agent`、供应商/模型归属），跨公司 / 不存在返回 404 而非泄露存在性。
- **密钥**：见 02-14、02-19。数据库 schema 中没有任何可存密钥值的列；Base URL 拒绝内嵌账号密码；`secret_ref` 必须是环境变量名格式（`sk-...` 之类字面密钥被 422 拒绝）。
- **日志**：沿用阶段 01 白名单 JSON 格式化器，事件文本需在 `_EVENTS` 中；本轮新增事件均未把供应商响应或密钥写入 extra。探测失败只记录稳定错误码与布尔用量标记。
- **密钥泄漏检查**：`test_no_secret_value_in_responses_database_or_logs` 用 canary 值遍历 5 个 API 响应与 19 张表全部列；前端构建产物不含密钥（页面源码无密钥输入框、审计详情服务端再脱敏一次）。
- **用量**：`provider_usage_records` 独立登记，未知用量 `usage_known=false` 且 token 为 `NULL`（测试 `test_missing_usage_is_unknown_not_zero` 明确反对记 0）。
- **数据保留**：所有关系 `ondelete="RESTRICT"`；无级联删除；审计、岗位版本、职责履历、绑定历史均只追加。本轮未删除任何真实数据卷或数据库。

## 模型验证

| 类别 | 内容 |
|---|---|
| 协议模拟（本轮实测） | `httpx.MockTransport` 注入，`backend/tests/test_organization.py` 与 `test_provider_adapter.py` 覆盖：正常 JSON（`{"ok": true}`）、错误密钥（401，供应商回显密钥的原文被丢弃）、超时（`ReadTimeout`）、无效 JSON（模型返回散文）。全部断言稳定错误码、`call_count` 与用量登记 |
| 模拟 API（边界说明） | 模拟传输只在测试进程中注入，生产 `create_app` 不带 transport，部署中不存在“切到演练”的开关 |
| 真实 API（未执行） | 未提供 DeepSeek 密钥。全程**未**发起任何真实供应商请求；模型名 `deepseek-v4.1-flash` 仅作为可编辑默认值，**不作为已可用能力**。验收环境显示“未配置” |
| 调用计数 | 真实供应商调用 **0 次**；测试内模拟调用受 `call_count <= 1` 约束 |

## 未通过与未执行

- **未通过：真实浏览器 200% 缩放。** 阻塞原因是本机自动化 Chrome 收不到 OS 注入输入（前台已建立但渲染器记录 0 个键盘事件）。影响：无法宣称“实际浏览器缩放已验收”。下一步：在可正常交互的桌面会话中手工按 Ctrl+“=”放大到 200%，或用具备真实输入通道的浏览器控制环境重跑 `--method=os-input`。等效布局门禁不能替代。
- **未执行：真实 DeepSeek 连通性**（无密钥）。因此 `AUTH_FAILED`/`RATE_LIMITED` 等分支只在协议模拟下验证过，未对真实服务确认。
- **未执行**：屏幕阅读器实测、真实 TLS/HTTPS 下的 Secure Cookie、全新机器 Compose 安装、压测、依赖漏洞扫描、跨标签页会话同步、真实网络乱序。均不在本阶段门禁必需项内，但不计为通过。
- **未实现（明确不在阶段范围）**：招聘与调岗审批（阶段 06）、任务调度与统一预算（阶段 03）、执行器（阶段 04）。组织编辑当前全部由所有者操作，页面无员工账号登录入口。
- 保留的既有警告：后端 4 个依赖弃用警告（starlette/httpx、alembic `path_separator`、per-request cookies），未隐藏、未升级依赖。

## 验收判断

阶段 02 的 19 项需求全部有实现与实测证据，其中持久化、初始化幂等与并发、组织防环、身份历史保留、owner 权限、密钥不泄漏、模拟供应商四类场景均为本轮真实执行通过。

仍有两项外部验证未关闭：真实浏览器 200% 缩放（阻塞，已定位到输入通道）与真实供应商连通性（无密钥）。按 `docs/quality-gates.md` 的完成定义，本阶段为**部分完成**：工程实现可交接，但上述两项不得记为通过，也不构成进入阶段 03 的完整门禁。阶段 03 未开始，未推送远程、未发布、未做生产部署。
