# 开工前阅读入口

**最新修复（2026-11-02）**：第二版复审的 5 项 P2 已修复；原样复跑修复前探针为后端 4 项、前端 1 项失败，修复后全量为后端 170 项、前端 93 项通过。见[修复验收](acceptance/phase-02-v2-fixes.md)、[交接](handoff/phase-02-v2-fixes.md)及[证据](acceptance/evidence/phase-02-v2-fixes/README.md)。原[复审报告](acceptance/phase-02-recheck.md)及其失败证据保持原样，代表修复前状态。外部验证未关闭，阶段 02 仍部分完成，阶段 03 未开始。

版本 1.7｜2026-11-02｜状态：阶段 01 R1–R7 修复回归通过；阶段 02 的 R1–R11 及第二版复审 5 项 P2 均有修复回归。真实浏览器 200% 缩放、真实供应商连通性与真实进程强杀恢复仍未验证。阶段 03 未开始。

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
| templates/ | ADR、验收和交接模板 |
| ../development-plans/ | 八阶段执行计划 |

## 冲突处理

用户当前明确要求优先于仓库文档。本文件及 ADR 记录产品决策范围；architecture.md 是技术基线，design-tokens.json 是视觉数值源；阶段计划不能擅自改动基线。
同一事项出现文档冲突时，先检查 docs/decisions 中最新已确认记录；没有结论则指出具体冲突，完成不受影响的工作，不静默选择方便的一边。
UI 文本中的尺寸须与 token 同步；样张是规范的可视化说明，不是第二个数值来源。

## 当前工程实况

正式工程位于 `backend/`（FastAPI、SQLAlchemy、Alembic、PostgreSQL）与 `frontend/`（Vue 3、TypeScript、Element Plus）。Compose 提供 web/api/worker/postgres/migrate，检查命令见 [README](../README.md)。Worker 仅心跳循环，无业务执行。

阶段 01：所有者初始化、登录、会话、CSRF、健康检查、公共组件与工作台外壳。
阶段 02：公司创建向导（一次生成五岗位五员工）、组织编辑器（资料/职责版本/主管防环/模型绑定与解绑/启停归档，身份与履历只追加）、模型中心（供应商/模型/员工分配三分离，DeepSeek 预设 + 自定义兼容接口，密钥仅环境变量引用）、统一模型适配接口（单次调用、总时限、结构化错误、JSON 输出校验、供应商元数据筛查）、两阶段探测（调用前提交意图，中断即“结果与用量未知”）、按维度的用量登记、审计写入与只读审计界面。

先读最新[第二版修复验收](acceptance/phase-02-v2-fixes.md)、[交接](handoff/phase-02-v2-fixes.md)、[证据索引](acceptance/evidence/phase-02-v2-fixes/README.md)，再读[第二版复审](acceptance/phase-02-recheck.md)。本轮修复锁内刷新与证据读取、历史指纹迁移、网络总超时、Unicode 元数据、部分保存刷新失败，并修复验收发现的窄屏抽屉问题。

前轮 [R1–R11 修复验收](acceptance/phase-02-fixes.md) 与 [交接](handoff/phase-02-fixes.md)、原始 [独立质检](acceptance/phase-02-review.md)、[质检交接](handoff/phase-02-review.md) 与[质检证据](acceptance/evidence/phase-02-review/README.md) 均保持原样，代表各自时点的状态。

再读原 [阶段 02 验收](acceptance/phase-02.md) 与 [阶段 02 交接](handoff/phase-02.md) 了解功能范围。阶段 01 的历史记录（[R7 修复验收](acceptance/phase-01-r7-fixes.md)、[R7 交接](handoff/phase-01-r7-fixes.md)、[R1–R6 修复验收](acceptance/phase-01-fixes.md)、[原独立质检](acceptance/phase-01-review.md)、[复检](acceptance/phase-01-recheck.md)）保持原样，代表各自时点的状态。

**仍未验证（不得记为通过）**：真实浏览器 200% 缩放（阶段 01 遗留、阶段 02 复现，修复轮仍未通过；阻塞原因与诊断见 `acceptance/evidence/phase-02/zoom-200-real.json`，等效布局门禁不能替代）；真实 DeepSeek 连通性（无密钥，仅模拟覆盖）；真实进程强杀后的恢复（修复轮用注入异常模拟中断，未做操作系统级终止）。

**阶段 02 现有三段迁移**：`7b41c9d2f0aa`（初始表）、`3f8d2c6a91b4`（配置版本/指纹、`PENDING` 状态、按维度用量）、`8c24a71d9e03`（旧指纹与能力证据修复，当前 head）。最后一项须在线执行、保留历史，降级不复活失效证据。升级前备份步骤、验证命令与降级注意事项见[第二版修复验收](acceptance/phase-02-v2-fixes.md)。

`company_os/` 是早期未经完整验证的思路草稿，不能当作正式后端。不删除草稿，不把它打入正式服务镜像。阶段 03—08 仍为计划，当前没有任务调度、预算账本、审批、隔离执行器或知识库实现。

## 开工方式

先核对上述当前验收与未验证项；后续实施须明确指定阶段，默认不进入阶段 03，也不重写阶段 01/02 已验收的工程。每阶段交接完成后再分配下一阶段。
已有 token、公共组件展示页和工作台壳应增量复用；不等到阶段 08 才统一界面。
UI 基线已替团队选择 Element Plus、浅色主题、蓝色主操作；开发 AI 无需再次发起组件库或主色选择题。

## 来源与独立设计

用户提供的 [参考 CLAUDE.md](https://github.com/CronusL-1141/AI-company/blob/master/CLAUDE.md) 与 [参考 AGENTS.md](https://github.com/CronusL-1141/AI-company/blob/master/AGENTS.md) 用于了解仓库约束文档的组织方式；本工程规则独立编写，不继承该仓库的框架、内部工具、工作流或历史事故约束。
本地颜色、尺寸和页面设计是本项目初始设计决策。后续调整记录原因与影响范围，不必把每个普通实现细节升级为审批。

