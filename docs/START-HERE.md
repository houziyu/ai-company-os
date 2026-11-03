# 开工前阅读入口

**最新（2026-11-03）**：供应商密钥改为**界面直接填写**并由服务端 AES-256-GCM 加密保存（[ADR-0006](decisions/0006-provider-secret-storage.md)，取代 ADR-0003 的密钥条目）。当前迁移头 `a7d2e5c81b64`；后端 258 项、前端 116 项测试通过；隔离 Compose 栈端到端验证了加密入库、不回显、留空不覆盖与换密钥作废旧证据。验收为**部分完成**：三视口截图（截图工具超时）、200% 缩放、真实 DeepSeek 调用未执行。见[验收](acceptance/provider-secret-storage.md)、[交接](handoff/provider-secret-storage.md)、[证据](acceptance/evidence/provider-secret-storage/README.md)。阶段 03 的修复验收见[此处](acceptance/phase-03-fixes.md)。

版本 2.0｜2026-11-03｜状态：阶段 01、02、03 及其修复回归通过，供应商密钥存储已变更。真实浏览器 200% 缩放、真实供应商连通性、阶段 02 的真实进程强杀恢复、新版界面截图仍未验证。阶段 04 未开始。

## 文档地图

| 文件 | 用途与权威范围 |
|---|---|
| ../AGENTS.md | 所有开发 AI 的共享协作规则 |
| ../CLAUDE.md | Claude Code 的阅读入口，引用 AGENTS.md |
| PRD.zh-CN.md | 产品目标、范围与业务需求 |
| architecture.md | 当前技术架构、部署与模块边界 |
| engineering-standards.md | API、数据、代码、配置及测试约定 |
| design/UI-SPEC.md | 视觉方向、布局、字体、颜色使用 |
| design/design-tokens.json | 颜色、尺寸与基础数值唯一来源 |
| design/COMPONENTS.md | 组件尺寸、状态与交互规范 |
| design/PAGES.md | 页面结构、按钮位置、信息与权限 |
| design/visual-reference.html | 离线静态样张，仅说明视觉，不是产品实现 |
| quality-gates.md | 分阶段质量门禁 |
| decisions/0001-baseline.md | 初始架构与设计决策、开放事项 |
| decisions/0003-phase-02-organization-and-model-centre.md | 阶段 02 数据模型、密钥引用与探测语义 |
| decisions/0004-phase-03-task-scheduling-and-budget.md | 阶段 03 队列、租约、fencing、预算与恢复语义 |
| decisions/0005-phase-03-recheck-concurrency-and-cursors.md | 阶段 03 复审修复：锁序、预算证据、游标兼容 |
| decisions/0006-provider-secret-storage.md | 供应商密钥改为界面填写并加密保存（取代 ADR-0003 的密钥条目） |
| acceptance/phase-03-fixes.md、handoff/phase-03-fixes.md | 阶段 03 审查 R1–R13 修复验收与交接 |
| acceptance/provider-secret-storage.md | 供应商密钥加密存储的验收与证据（当前） |
| templates/ | ADR、验收和交接模板 |
| ../development-plans/ | 八阶段执行计划 |

## 冲突处理

用户当前明确要求优先于仓库文档。本文件及 ADR 记录产品决策范围；architecture.md 是技术基线，design-tokens.json 是视觉数值源；阶段计划不能擅自改动基线。
同一事项出现文档冲突时，先检查 docs/decisions 中最新已确认记录；没有结论则指出具体冲突，完成不受影响的工作，不静默选择方便的一边。
UI 文本中的尺寸须与 token 同步；样张是规范的可视化说明，不是第二个数值来源。

## 当前工程实况

正式工程位于 `backend/`（FastAPI、SQLAlchemy、Alembic、PostgreSQL）与 `frontend/`（Vue 3、TypeScript、Element Plus）。Compose 提供 web/api/worker/postgres/migrate，检查命令见 [README](../README.md)。Worker 是阶段 03 的持久队列消费者：领取任务、运行确定性演练处理器、维护租约与心跳、恢复过期租约；仍不执行生成代码、不调用真实模型。

阶段 01：所有者初始化、登录、会话、CSRF、健康检查、公共组件与工作台外壳。
阶段 02：公司创建向导（一次生成五岗位五员工）、组织编辑器（资料/职责版本/主管防环/模型绑定与解绑/启停归档，身份与履历只追加）、模型中心（供应商/模型/员工分配三分离，DeepSeek 预设 + 自定义兼容接口，密钥仅环境变量引用）、统一模型适配接口（单次调用、总时限、结构化错误、JSON 输出校验、供应商元数据筛查）、两阶段探测（调用前提交意图，中断即“结果与用量未知”）、按维度的用量登记、审计写入与只读审计界面。

阶段 03：项目与任务（看板/列表、创建、取消、重试）、独立执行记录与执行步骤、持久事件与结构化消息、SSE 事件流（提交顺序游标 + 断线补读）、公司/项目/任务三层 Token 预算账户、预留与用量账本（未知用量保守计入并标记待核对）、PostgreSQL 持久队列（`SKIP LOCKED` + 租约 + 心跳 + fencing token）、双 Worker 领取、进程退出后的中断恢复、有限自动重试、公司暂停、确定性演练处理器（页面明确标注非真实模型输出）。每一步开始前通过统一停止门禁（暂停/取消/租约/额度）；恢复时额度不足记 `BLOCKED` 而不中断 Worker。阶段 02 的连通性探测已纳入公司预算；建公司前的探测不占额度并如实说明。

先读[阶段 03 修复验收](acceptance/phase-03-fixes.md)、[修复交接](handoff/phase-03-fixes.md)与[修复证据索引](acceptance/evidence/phase-03-fixes/README.md)，再读原[阶段 03 验收](acceptance/phase-03.md)、[交接](handoff/phase-03.md)、[证据索引](acceptance/evidence/phase-03/README.md)、[独立审查](acceptance/phase-03-review.md)与 [ADR-0004](decisions/0004-phase-03-task-scheduling-and-budget.md)。再读阶段 02 的[第二版修复验收](acceptance/phase-02-v2-fixes.md)、[交接](handoff/phase-02-v2-fixes.md)、[证据索引](acceptance/evidence/phase-02-v2-fixes/README.md)与[第二版复审](acceptance/phase-02-recheck.md)。

前轮 [R1–R11 修复验收](acceptance/phase-02-fixes.md) 与 [交接](handoff/phase-02-fixes.md)、原始 [独立质检](acceptance/phase-02-review.md)、[质检交接](handoff/phase-02-review.md) 与[质检证据](acceptance/evidence/phase-02-review/README.md) 均保持原样，代表各自时点的状态。

再读原 [阶段 02 验收](acceptance/phase-02.md) 与 [阶段 02 交接](handoff/phase-02.md) 了解功能范围。阶段 01 的历史记录（[R7 修复验收](acceptance/phase-01-r7-fixes.md)、[R7 交接](handoff/phase-01-r7-fixes.md)、[R1–R6 修复验收](acceptance/phase-01-fixes.md)、[原独立质检](acceptance/phase-01-review.md)、[复检](acceptance/phase-01-recheck.md)）保持原样，代表各自时点的状态。

**仍未验证（不得记为通过）**：真实浏览器 200% 缩放（阶段 01 遗留、阶段 02 复现；阻塞原因与诊断见 `acceptance/evidence/phase-02/zoom-200-real.json`，等效布局门禁不能替代）；真实 DeepSeek 连通性（无密钥，仅模拟覆盖）；阶段 02 的真实进程强杀恢复（阶段 02 修复轮用注入异常模拟中断）。

阶段 03 **另有独立**的中断恢复证据，且是在操作系统层面验证的：真实 worker 进程被 `taskkill /F` 终止、worker 容器被 `docker kill`、整栈停机再启动，三种情形下任务被标记 `INTERRUPTED` 并在存活进程接手后以新执行 ID 完成。这**不能**回溯替代阶段 02 的那一项结论。修复轮又补跑了真实 uvicorn + 两个 worker 进程的 10 项检查（含领取后暂停、运行中取消），以及**真实 `deploy/nginx/default.conf` 代理后的 SSE 断线补读**（代理停机期间提交事件，恢复后补发，无重复）。仍未执行：真实供应商调用、真实支付币值、完整 Compose 整链、真实浏览器 200% 缩放、R13 的真实浏览器断网复现。

阶段 03 的内部机制：任务领取用 `SKIP LOCKED` + 条件 `UPDATE`；每一步开始前在同一事务内通过统一停止门禁（公司暂停、取消、有效租约、任务额度）；所有执行写入在 `company → task → execution` 锁序下校验 fencing 与租约到期；恢复把过期尝试标为 `INTERRUPTED` 并按调用证据决定释放/保守结算，重试额度不足时记 `BLOCKED` 而不中断 Worker；尝试用量由步骤行重建，任一未知步骤即整次未知；公司/项目规划封套与任务调用预留分三层结算；SSE 使用提交顺序发布游标（`domain_events.published_seq`）而非主键，晚提交事件会补发；查询参数同时接受 camelCase 与 snake_case；前端幂等键绑定到一次逻辑提交，失败重试沿用原键。

**迁移历史（当前 head `e5b7c9d1a2f4`）**：`6a790d8c2b88`（阶段 01）、`7b41c9d2f0aa`（阶段 02 公司/组织/模型表）、`3f8d2c6a91b4`（配置版本/指纹、`PENDING`、按维度用量）、`8c24a71d9e03`（旧指纹与能力证据修复）、`c41f7b93e2a5`（阶段 03 任务、执行、事件、消息、预算）、`e5b7c9d1a2f4`（阶段 03 修复：事件提交顺序发布游标，加列/索引/按 id 回填）。`8c24a71d9e03` 须在线执行并保留历史；`c41f7b93e2a5` 为既有公司补建额度 0 的公司预算账户——升级后**需要运营显式设置额度**，否则新任务会被拒绝。降级 `e5b7c9d1a2f4 → c41f7b93e2a5` 只删除发布游标（SSE 回退为按主键排序，可能漏事件），不删除业务数据；降级到 `8c24a71d9e03` 会删除阶段 03 全部表并把暂停状态复位，只在无任务数据时安全。完整命令见[修复验收](acceptance/phase-03-fixes.md)与[第二版修复验收](acceptance/phase-02-v2-fixes.md)。

`company_os/` 是早期未经完整验证的思路草稿，不能当作正式后端。不删除草稿，不把它打入正式服务镜像。阶段 04—08 仍为计划：当前没有隔离执行器、五岗位交付闭环、提案审批、知识库或长期项目记忆。任务的「运行」是确定性演练处理器，不是真实模型产出，也不能运行用户代码。

## 开工方式

先核对上述当前验收与未验证项；后续实施须明确指定阶段，默认不进入阶段 04，也不重写阶段 01/02/03 已验收的工程。每阶段交接完成后再分配下一阶段。
已有 token、公共组件展示页和工作台壳应增量复用；不等到阶段 08 才统一界面。
UI 基线已替团队选择 Element Plus、浅色主题、蓝色主操作；开发 AI 无需再次发起组件库或主色选择题。

## 来源与独立设计

用户提供的 [参考 CLAUDE.md](https://github.com/CronusL-1141/AI-company/blob/master/CLAUDE.md) 与 [参考 AGENTS.md](https://github.com/CronusL-1141/AI-company/blob/master/AGENTS.md) 用于了解仓库约束文档的组织方式；本工程规则独立编写，不继承该仓库的框架、内部工具、工作流或历史事故约束。
本地颜色、尺寸和页面设计是本项目初始设计决策。后续调整记录原因与影响范围，不必把每个普通实现细节升级为审批。

