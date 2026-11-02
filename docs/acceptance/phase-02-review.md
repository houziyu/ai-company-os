# 阶段 02 独立质检：代码与业务逻辑

日期：2026-10-30（Asia/Shanghai）  
对应阶段：02，公司模板、组织编辑器、模型中心。  
代码版本/分支：当前目录没有 `.git`，不能作提交差异审查；本轮检查当前文件快照，摘要见 [source-sha256.json](evidence/phase-02-review/source-sha256.json)。  
环境：Windows、Python 3.13.12、Node 24.21.0、pnpm 10.34.9、Docker 29.6.2；真实 PostgreSQL 17，独立容器 `acos-phase02-review-20261030`，仅监听 `127.0.0.1:55434`。  
本报告结论：**失败，存在需要修复的安全与持久化问题。现有功能测试通过，不能作为阶段 02 已满足全部业务边界的证明。**

本轮只审查与复现；未修改应用实现、迁移、依赖、原验收报告或原交接记录。新增的是本报告、质检交接和独立证据。未进入阶段 03。

## 需求与证据

| 检查 | 实际方法/命令 | 实际结果 | 证据 |
|---|---|---|---|
| 后端静态检查 | 在 `backend` 执行 `.venv/Scripts/python.exe -m ruff check .`、`-m ruff format --check .`、`-m mypy app` | 通过；55 文件格式检查，42 个源文件类型检查 | [检查摘要](evidence/phase-02-review/checks-summary.json) |
| 原有后端回归 | 在独立 PostgreSQL 上执行 `.venv/Scripts/python.exe -m pytest -q` | **141 passed，4 warnings，162.79 秒** | [首轮记录](evidence/phase-02-review/baseline-first-attempt.txt)；[详细复跑](evidence/phase-02-review/backend-baseline.txt) |
| 原有前端检查 | 在 `frontend` 执行 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm exec vite build` | 全通过；**83 passed / 9 文件**，构建成功 | [检查摘要](evidence/phase-02-review/checks-summary.json) |
| 新增后端边界探针 | 真实迁移数据库 + API + 供应商模拟传输；超时项使用真实本机 HTTP 服务 | **11 个正确性断言失败**，对应 R1–R9，其中 R5 三个输入 | [探针](evidence/phase-02-review/test_review_probes.py)、[原始输出](evidence/phase-02-review/backend-probes.txt) |
| 新增前端交互探针 | 挂载真实 Vue 页面、API 客户端和组件；仅替换 HTTP 响应 | **3 个正确性断言失败**，对应 R8、R10、R11 | [探针](evidence/phase-02-review/frontend-probes.spec.ts)、[原始输出](evidence/phase-02-review/frontend-probes.txt) |

新增探针的失败表示已复现缺陷，不是运行环境错误，也不是生产代码已修复。前后端 R8 验证同一个问题，不能算两个独立缺陷。合计 **11 个独立发现：3 个 P1、7 个 P2、1 个 P3**。

## 发现与修复建议

### R1 · P1：供应商响应的模型字段能把密钥写入数据库并返回浏览器

位置：[adapter.py:292](../../backend/app/providers/adapter.py#L292)、[model_centre_service.py:640](../../backend/app/services/model_centre_service.py#L640)。

适配器直接信任响应 `body.model`，随后写入 `model_probes.resolved_model_name` 和 `provider_models.resolved_name`，探测接口与模型中心接口原样返回。只丢弃错误正文不足以覆盖这个出口。

复现：测试供应商把收到的 `Authorization` 中的虚构 canary 密钥放入 `model`，其余响应完全合法。探测返回 200；**探测响应、模型中心响应和数据库三处均出现 canary**。本轮没有使用真实密钥，不意味着真实供应商已发生泄漏，但该响应处理路径确实允许泄漏。

建议：所有供应商返回的可持久化/可返回字段统一做凭据检查与类型、长度限制；与当前秘密相同或包含秘密的模型标识丢弃，不能只检查错误正文或固定关键词。保留无敏感信息的调用及用量证据。补充成功响应与失败响应的回显测试。

### R2 · P1：探测发起前未提交记录，进程中断会留下“已经调用、没有证据”

位置：[model_centre_service.py:534](../../backend/app/services/model_centre_service.py#L534)、[organization_repository.py:444](../../backend/app/repositories/organization_repository.py#L444)、[session.py:60](../../backend/app/db/session.py#L60)。

`ProbeRepository.create()` 只 `flush()`，探测、结果、用量和审计都在同一个请求事务内，最终统一提交。函数注释声称“调用前写入，崩溃后留下 call_count=1”，实际创建值还是 0，且事务尚未提交。

复现：在供应商收到请求时，使用第二个独立数据库连接查询探测表，**可见行数为 0**。因此进程退出或事务回滚会失去调用意图；R5 的实际回滚同时证明已调用后的记录可以全部消失。

建议：调用前在短事务中提交可恢复的探测意图/尝试标识；调用后用另一短事务登记结果和用量。中断按“结果及用量未知”处理，不能自动重发付费请求。该修复不要求提前实现阶段 03 的完整调度系统。

### R5 · P1：供应商异常元数据能使已发生的调用和用量整体回滚

位置：[adapter.py:179](../../backend/app/providers/adapter.py#L179)、[adapter.py:292](../../backend/app/providers/adapter.py#L292)、[model_centre_service.py:640](../../backend/app/services/model_centre_service.py#L640)。

返回模型名仅检查是否为字符串，Token 仅检查非负整数，未对齐存储字段的长度和整数范围。

复现：分别返回 **161 字符模型名**、**prompt_tokens=2³¹**，其余响应正常。两个请求均变为 **503 DATABASE_NOT_READY**，实际上数据库健康；探测表和用量表均为 **0 行**。这会把供应商输入问题误报成部署故障，并抹去已发生的调用。

另一个同边界问题：返回空 `choices`、同时带有效 `usage={prompt_tokens:21, completion_tokens:7}`，适配器在提取 usage 前就抛错，记录中已报告用量变成未知。本轮第三个 R5 探针已复现。

建议：外部响应进入业务层前完成约束校验；用量解析独立于消息信封校验，合法用量应保留；非法字段返回稳定供应商错误，并持久化一次调用及未知用量，避免让数据库异常决定响应语义。

### R3 · P2：供应商地址改变后仍沿用旧地址的能力验证

位置：[model_centre_service.py:281](../../backend/app/services/model_centre_service.py#L281)。

更新 `base_url`、类型或密钥引用不失效关联模型的能力和最近探测状态，也没有配置版本来判断证据属于哪次配置。

复现：地址 A 探测成功后，把地址改为从未调用的 B；模型仍返回 **supportsJsonOutput=true、lastProbeOutcome=SUCCESS**。用户看到的“探测已验证”已不对应当前配置。

建议：探测证据绑定供应商/模型配置版本或指纹；配置变化将当前验证置为“未验证”，保留旧探测历史。晚到的旧配置探测结果也应拒绝更新新配置的能力标记。

### R4 · P2：部分未知用量被显示为已计量的 0

位置：[adapter.py:312](../../backend/app/providers/adapter.py#L312)、[organization_repository.py:510](../../backend/app/repositories/organization_repository.py#L510)。

`usage_known` 使用输入/输出任一非空的 OR 判断；汇总时又对空值使用 `or 0`。

复现：供应商只报告输入 21 Token，不报告输出。探测明细的输出正确为 null，但模型中心汇总为 **measuredCalls=1、unmeasuredCalls=0、completionTokens=0**。这是实际未知被改写成零。

建议：按输入/输出维度保存完整性；部分已知不能称完整计量。汇总保留“已报告值/未报告项”的区别，不能用 0 填补缺失值。阶段 03 接入账本前必须关闭此问题。

### R6 · P2：“硬超时”实际仅限制单次网络等待

位置：[adapter.py:240](../../backend/app/providers/adapter.py#L240)。

`httpx.Timeout(timeout)` 没有为整个探测设置总截止时间。供应商只要持续以低于 read timeout 的间隔发送少量数据，请求就可以超出配置时间继续等待。

复现：真实本机 HTTP 模拟服务分块缓慢返回，每块间隔 0.24 秒；配置 **1 秒**，实测约 **3.989 秒**后成功返回，**没有 TIMEOUT**。该时间包含客户端建立成本，不应解读为所有环境的固定耗时；“超出 1 秒而仍成功”的事实已确认。

建议：总截止时间涵盖客户端建立、连接、读取和解析；同时限制响应大小，确保到期确实终止外部 I/O，避免仅停止等待而后台调用继续。与前置代理超时对齐，避免 UI 提前失败后重复付费测试。

### R7 · P2：供应商旧表单静默覆盖新配置

位置：[schemas_organization.py:191](../../backend/app/api/schemas_organization.py#L191)、[model_centre.py:265](../../backend/app/api/v1/model_centre.py#L265)、[model_centre_service.py:255](../../backend/app/services/model_centre_service.py#L255)。

供应商没有配置版本；路由固定传 `expected_version=None`，服务参数完全未使用。行锁只能串行保存，不能判断表单是否过期。模型配置更新同样没有版本前置条件，本轮具体复现对象是供应商。

复现：A、B 读取同一供应商；A 改名称并保存，B 使用原名称修改地址并保存。两次均 200，A 的名称被恢复成旧值，无冲突提醒。

建议：供应商和模型增加版本字段/条件更新；更新 API 必须携带读取到的版本，冲突返回 409；前端保留草稿并引导刷新。

### R8 · P2：员工资料部分成功后，改绑失败会使后续保存一直冲突

位置：[OrganizationView.vue:249](../../frontend/src/views/OrganizationView.vue#L249)。

同一个“保存员工资料”先请求资料更新，再请求模型分配；首个响应被丢弃。第二步失败时没有刷新或回填已提交的新版本。

复现：资料更新 200（数据库版本 1→2），改绑失败；再次点击保存仍提交版本 1，返回 409。API 顺序复现与真实 Vue 页面组件复现一致，组件两次发送的版本为 **[1,1]**，服务端已是 2。

建议：要么提供原子编辑命令，要么明确分开保存动作；如维持分步保存，第一步成功立即更新本地实体/版本，第二步失败明确提示已保存范围，保留尚未保存草稿并允许继续修正。

### R10 · P2：键盘无法进入模型中心第三个标签

位置：[ModelsView.vue:388](../../frontend/src/views/ModelsView.vue#L388)。

所有标签的右方向键都硬编码为 `models`，左方向键都为 `providers`；同时仅当前标签可 Tab 聚焦，切换后没有移动焦点。

复现：供应商标签聚焦后连续按两次右方向键，选中项停在第二项、焦点仍在第一项；员工分配标签无法通过该键盘路径到达。真实页面组件探针确认 **selected=1、focused=0**。

建议：按标签索引处理左右/Home/End，同时移动选中和焦点，维持可访问的 roving tabindex；补充完整三标签往返测试。

### R11 · P2：“未绑定”选项没有任何实际解绑行为

位置：[OrganizationView.vue:257](../../frontend/src/views/OrganizationView.vue#L257)、[OrganizationView.vue:602](../../frontend/src/views/OrganizationView.vue#L602)。

下拉允许对已绑定员工选择“未绑定”，但保存逻辑仅在 `draftModelId !== null` 时发送绑定命令，后端也没有关闭当前绑定的对应契约。

复现：已绑定员工选择“未绑定”并保存，**模型分配请求数量为 0**；重新读取仍是原绑定，页面没有解释该选择不生效。

建议：实现带历史保留的显式解绑（结束当前绑定而非删除）；如解绑不在本阶段承诺中，已绑定时不要提供可选择但不生效的“未绑定”。

### R9 · P3：供应商备注无法通过界面清空

位置：[ModelsView.vue:198](../../frontend/src/views/ModelsView.vue#L198)、[model_centre_service.py:271](../../backend/app/services/model_centre_service.py#L271)。

前端清空备注时发送 `notes:null`，后端把 null 解释成“保持原值”。

复现：先设 `old-note`，再 PATCH `notes:null`，返回 200 但内容仍为 `old-note`。

建议：区分“字段未提交”与“显式 null 清空”（例如 Pydantic `model_fields_set`），保持前后端契约一致。

## UI 检查

本轮没有 UI 实现变更。执行的是 Vue 组件交互测试，不是实际浏览器布局验收。

未重新运行 1440×900、1024×768、390×844、360px 截图、真实浏览器键盘全流程或实际 200% 缩放；原阶段 02 截图仅作为历史材料，不计本轮实测通过。R10 为已复现的键盘逻辑缺陷。

## 数据与安全

- 原有 141 项回归通过，覆盖公司单例/重复提交、并发创建、已有防环、身份与历史保留、所有者认证/CSRF、会话等已建立路径。本结论只覆盖这些测试的实际断言。
- 全部数据库探针使用新容器中的迁移模板/每测试克隆库；未连接既有 Compose 业务库、未读取 `.env` 内容、未删除现有卷或真实数据。
- 原有“密钥不泄漏”测试通过，但覆盖不足，不能否定 R1 的成功响应回显证据。
- 未对真实数据进行迁移、降级、备份恢复或生产部署；新测试库升级到当前迁移头已实测。阶段 03 预算和阶段 04 执行隔离尚未实现，不把它们列为本轮缺陷。

## 模型验证

| 类别 | 本轮范围 |
|---|---|
| 模拟传输 | 后端 API 探针使用 `httpx.MockTransport`；凭据为测试 canary；每个探测至多一次调用，无外部付费请求 |
| 真实协议模拟服务 | R6 使用本机临时 HTTP 服务验证缓慢分块响应；无真实模型、无真实密钥 |
| 前端模拟 API | 3 项交互探针替换 fetch 响应；不作为服务端持久化验证，R8 另有独立 PostgreSQL 证据 |
| 真实供应商 API | **未执行**；未访问 DeepSeek 或其他外部供应商，不声称验证真实模型名称、能力或价格 |

## 未通过与未执行

R1–R11 均已复现，未修复。完整 Compose 浏览器端到端、实际缩放、HTTPS、真实供应商、真实进程强杀恢复均未新增验证；R2 的崩溃后果来自已实测的事务可见性和当前事务回滚逻辑，不能表述为本轮做过真实进程强杀。

## 验收判断

**阶段 02 本次独立质检不通过。** 优先修复 R1/R2/R5；R3/R4/R6/R7/R8/R10/R11 是可见业务或工程约束缺陷，应在交接前关闭；R9 为低优先级功能问题。

阶段 03 会直接复用适配器、探测用量和模型配置，应先修复安全、持久化、用量完整性与配置证据归属，再进行依赖这些能力的验收。本报告没有授权或实施阶段 03。
