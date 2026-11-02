# 阶段 02 修复验收：独立质检 R1–R11

日期：2026-10-30（Asia/Shanghai）
对应阶段：02（公司模板、组织编辑器、模型中心），修复独立质检发现的问题。未进入阶段 03。
代码版本/分支：当前工作区无 `.git`，不能提供提交哈希；质检时的源码指纹见[原证据](../acceptance/evidence/phase-02-review/source-sha256.json)。
环境：Windows 10.0.26100、Python 3.13.12、Node 24.21.0、pnpm 10.34.9、Docker 29.6.2、Chrome 156.0.8078.48；隔离测试 PostgreSQL 17（`127.0.0.1:55434`），隔离 Compose 栈（`127.0.0.1:18083`）。依赖未升级。
本报告结论：**11 项发现全部修复并有正式回归；阶段 02 因此前遗留的两项外部验证（真实浏览器 200% 缩放、真实供应商连通性）仍为部分完成。**

## 工作顺序与证据

先按顺序阅读 `phase-02-review.md`、`phase-02-review.md` 交接、质检证据索引、原阶段 02 验收/交接与 ADR-0003，再核对代码，然后把质检探针在**修复前**原样复跑：后端 **11 项失败**、前端 **3 项失败**，与报告一致。修复前输出保留在
[probe-before.txt](evidence/phase-02-fixes/probe-before.txt) 与
[frontend-probes-before.txt](evidence/phase-02-fixes/frontend-probes-before.txt)；原质检目录未改动。

修复后，同一批缺陷转成正式回归 `backend/tests/test_review_fixes.py`（18 项）与
`frontend/src/__tests__/review-fixes.spec.ts`（8 项）。四处断言按新契约调整，理由逐条写在下面 R3/R6/R7/R8 行内，均未删除缺陷覆盖。

## R1–R11 逐项结果

| ID | 修复位置 | 修复做法 | 回归测试 | 结果 |
|---|---|---|---|---|
| **R1** P1 响应回显密钥 | `providers/adapter.py: sanitize_provider_text` | 供应商返回的模型标识在**写入与返回之前**统一筛查：非字符串/空白/含控制字符/超长/包含当前凭据（或凭据的 12 字符窗口）一律丢弃；丢弃不改变调用记录 | `test_r1_provider_model_echo_must_not_persist_or_return_secret`、`test_r1_partial_credential_echo_is_also_dropped` | 通过：探测响应、模型中心响应与数据库均无 canary；`outcome=SUCCESS`、`callCount=1` 仍保留调用证据 |
| **R2** P1 调用前未提交 | `services/model_centre_service.py: _commit_probe_intent`、`repositories/organization_repository.py: create_intent` | 调用前用**独立 session 短事务**提交 `outcome='PENDING'`、`call_count=1` 的意图行；结果用请求事务写回。进程中断则留下 PENDING，读作“结果与用量未知”，**不自动重发** | `test_r2_probe_intent_is_committed_before_the_network_call`、`test_r2_interrupted_probe_stays_pending_and_is_read_as_unknown` | 通过：调用进行中另一连接可见 `(1, PENDING, 1)`；模拟中断后行为 PENDING、`finished_at` 为空、无用量行、UI `usageKnown=false` |
| **R5** P1 异常元数据回滚 | `adapter.py: _coerce_usage_int / _extract_probe_content / _read_capped_body`、`_finish_row` | 进入业务层前完成范围校验（模型名 ≤160、Token ≤ PostgreSQL INTEGER 上限）；usage 解析独立于 `choices` 校验；非法信封返回稳定错误并**仍登记调用与可用用量** | `test_r5_untrusted_response_preserves_probe_and_usage`（3 个输入） | 通过：161 字符模型名、`prompt_tokens=2³¹`、空 `choices` 均返回 200、各 1 条 probe + 1 条 usage + 1 条审计；不再出现 503；空 `choices` 时仍保留 `prompt_tokens=21` |
| **R3** P2 旧地址沿用验证 | `providers`/`provider_models` 新增 `config_fingerprint`、`probe_config_fingerprint`；`update_provider` 变更即失效 | 指纹覆盖 kind/baseUrl/secretRef/timeout（不含名称、备注、状态）；配置变化清空当前能力字段并标记需重测；迟到的旧配置结果不更新新配置；旧探测行保留 | `test_r3_provider_endpoint_change_must_invalidate_capability`、`test_r3_renaming_a_provider_keeps_its_probe_evidence` | 通过：改地址后 `supportsJsonOutput=null`、`lastProbeOutcome=null`，探测历史仍在；仅改名不失效。**契约调整**：该探针的 PATCH 现需携带 `expectedVersion`（R7 的通用要求），断言未改 |
| **R4** P2 未知用量显示为 0 | `provider_usage_records` 新增 `prompt_tokens_known`/`completion_tokens_known`；`summarize` 改为三维度 | `usage_known` 仅在**全部维度**已知时为真；汇总分别给出完整/部分/未计量，并对每个维度给出“未报告调用数”；缺失维度保持 `null` | `test_r4_partial_usage_does_not_turn_unknown_completion_into_zero`、`test_r4_fully_reported_usage_counts_as_measured` | 通过：只报输入时 `completionTokens=null`、`partiallyMeasuredCalls=1`、`completionTokensUnreportedCalls=1`；数据库 `(false,true,false)` |
| **R6** P2 超时非总时限 | `adapter.py` 改为 `client.stream` + 截止时间逐块检查 + 256 KiB 上限 | 配置值成为覆盖连接、读取、解析的**总预算**；超预算立即终止传输；响应体过大判为无效响应 | `test_r6_timeout_bounds_total_elapsed_time`、`test_r6_oversized_response_is_rejected` | 通过：分块慢速服务下 1 秒预算 → `TIMEOUT`，实测 1.51–1.65 秒，同时实测同机固定开销 2.08–2.15 秒（故探针原来的 1.6 秒硬阈值不可能达到，已改为“固定开销 + 预算 + 余量”） |
| **R7** P2 旧表单覆盖新配置 | `providers.version`/`provider_models.version`；`ProviderUpdateRequest.expectedVersion` 与 `ModelUpdateRequest.expectedVersion` **必填** | 更新必须携带读取到的版本，不匹配返回 409 `CONCURRENT_UPDATE`；不能声明版本的表单直接 422，不再被默认为“最新” | `test_r7_stale_provider_form_is_rejected`、`test_r7_stale_model_form_is_rejected`、前端 `review-fixes.spec.ts` 的 provider 表单用例 | 通过：第二次提交 409，新值保留；无版本 422。**契约调整**：探针原请求没有 `expectedVersion`，现返回 422 而非 200/409；报告以“过期表单返回 409”为主断言 |
| **R8** P2 部分成功后永久冲突 | `frontend/src/views/OrganizationView.vue: saveProfile` | 资料保存成功后**立即刷新并回填版本**再执行第二步；第二步失败时明确提示“资料已保存（版本 N），模型绑定未保存”，保留未保存的绑定选择；再次保存使用新版本 | 前端 `after a successful profile save the next attempt uses the new version`（断言提交版本 `[1,2]`，服务端 1→2→3）、`keeps the unsaved model choice when the binding step fails`；后端 `test_r8_...` 固定服务端契约 | 通过：修复前为 `[1,1]` 且重试 409，现在第二次提交用 2 并成功；警告区显示部分保存范围。**契约调整**：后端测试改为断言“成功保存返回新版本 + 重放旧版本得 409 + 重新读取后重试成功”，替代原先“任何情况下都不许 409”的表述 |
| **R10** P2 键盘到不了第三个标签 | `frontend/src/views/ModelsView.vue`：`tabItems`、`selectTab`、`onTabKeydown` | 按索引处理 ←/→（循环）、Home/End，并**同时移动选中与焦点**（`nextTick` 后聚焦），维持 roving tabindex | 前端 4 项（右移/左移循环、Home/End、tabindex 数组）与浏览器检查 `model centre tab keyboard` | 通过：三次右移可从供应商到达员工分配且焦点跟随；`tabindex` 为 `['-1','0','-1']` |
| **R11** P2 “未绑定”无行为 | 新增 `POST /agents/{id}/model-unbind`、`AssignmentsRepository.close_current`、`OrganizationView` 解绑分支 | 显式解绑设置 `ended_at` 结束当前绑定而**不删除行**，写审计；界面选择“未绑定”即调用该端点；无绑定时为幂等空操作 | 后端 `test_r11_unbind_ends_binding_and_keeps_history`、前端 `selecting 未绑定 sends an explicit unbind request`、浏览器 `unbind persists after reload` | 通过：解绑后当前状态为空、历史行 `ended_at` 非空、刷新后仍为“未绑定模型”；未发出任何 `/model-assignment` |
| **R9** P3 备注无法清空 | `ProviderUpdateRequest` + 路由 `notes_provided="notes" in payload.model_fields_set` + `update_provider(notes_provided=...)` | 区分“字段未提交”与“显式 null 清空” | `test_r9_provider_notes_can_be_cleared`、前端 provider 表单用例 | 通过：`notes: null` 清空；省略 `notes` 时保留原值。**契约调整**：探针 PATCH 现需 `expectedVersion`，断言未改 |

后端 pytest 总数由 **141 → 159**（新增 18）；前端由 **83 → 91**（新增 8）。原有 141/83 项全部保持通过，没有删除或跳过任何旧断言。

## 数据库变更、备份与验证

新增迁移 `3f8d2c6a91b4_phase_02_review_fixes.py`（`down_revision = 7b41c9d2f0aa`）。**只加列与约束，不建新表，不删除数据。**

- `providers`：+`version`、+`config_fingerprint`、+`CHECK version >= 1`。
- `provider_models`：+`version`、+`probe_config_fingerprint`、+`CHECK version >= 1`。
- `model_probes`：+`config_fingerprint`；`outcome` 约束由 `SUCCESS/FAILURE` 放宽为 `PENDING/SUCCESS/FAILURE`；+索引 `ix_model_probes_outcome_started`。
- `provider_usage_records`：+`prompt_tokens_known`、+`completion_tokens_known`。

**数据回填**（迁移内 SQL，已实测）：历史用量行按实际存储推导每维标志，并把 `usage_known` 降级为“所有维度已知”。实测一条 `prompt=21, completion=NULL, usage_known=true` 的旧行升级为 `usage_known=false, prompt_known=true, completion_known=false`。历史探测的 `config_fingerprint` 为空串，与任何真实指纹不等，因此被如实视为过期证据——这是迁移刻意的保守选择，没有伪造归属。

**实测往返**（`migration-path.txt`）：`7b41c9d2f0aa` → `3f8d2c6a91b4` → 降级 → 再升级，表数保持 19，约束与列按预期出现/消失，升级后 `PENDING` 行为 0（迁移不捏造调用意图）。

**升级步骤（含备份）**：

```bash
# 1. 备份（Compose 部署）
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --format=custom > backup-$(date -u +%Y%m%dT%H%M%SZ)-pre-revfix.dump
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -tc "SELECT version_num FROM alembic_version;"   # 记录升级前版本

# 2. 升级（migrate 服务是独立一次性步骤，不要多副本并发迁移）
docker compose up -d --build migrate
docker compose ps migrate                          # 期望 Exited (0)

# 3. 验证
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -tc "SELECT version_num FROM alembic_version;"   # 期望 3f8d2c6a91b4
curl -fsS http://127.0.0.1:8080/api/v1/health/ready
```

回退：`alembic downgrade 7b41c9d2f0aa` 只删除本迁移新增的列/约束并恢复旧的 `outcome` 约束。若已有 `PENDING` 行，降级会因旧约束拒绝 `PENDING` 而失败——这是有意的：先确认这些行确实无需保留意图证据，再人工处理，不要为通过降级而删除调用记录。回退前同样先备份。

## 检查结果

| 命令 | 结果 | 证据 |
|---|---|---|
| 后端 `ruff check` / `ruff format --check` / `mypy app` | 全部通过（59 文件格式、42 源文件类型） | `static-checks.txt` |
| 后端 `pytest -q`（真实 PostgreSQL） | **159 passed，4 warnings，175.89 秒** | `backend-pytest.txt` |
| 前端 `pnpm check` | tokens/lint/typecheck 通过；**91 passed / 10 文件**；构建通过 | `frontend-checks.txt` |
| 隔离 Compose 栈 `scripts/verify_phase02.py` | **34 项检查全部通过**（原 31 项 + 过期表单 409、备注清空、地址变更后能力未验证） | `e2e-compose.txt` |
| 浏览器 `scripts/verify_phase02_ui.cjs` | **20 项通过**（新增键盘标签路径与解绑持久化） | `ui-checks.txt` |
| 迁移路径脚本 | 见上节 | `migration-path.txt` |
| 200% 缩放**等效布局**门禁 | 通过 | `zoom-200-equivalent.txt` |
| 200% **真实浏览器**缩放 | **未通过（阻塞，原因未变）** | `zoom-200-real-attempt.txt` |

## UI 检查

浏览器检查在隔离 Compose 栈（真实 API 与 PostgreSQL，`DEEPSEEK_API_KEY` 为空）上执行，**20 项全部通过，无未捕获页面异常**。

- 视口：1440×900、1024×768、390×844、360×800 全部截图并断言无整页横向溢出。
- 键盘：员工行焦点环 + Enter 打开抽屉、Escape 关闭、注销确认焦点返回；**新增**模型中心三个标签可仅用键盘到达（→/←/Home），选中与焦点同步。
- 状态：向导四步、组织层级/抽屉/改名后、模型中心空态/供应商表单/模型/分配、审计列表与筛选、解绑后组织、移动端导航抽屉。
- 尺寸与对比度：实测 `.app-button` 高度 32/40/48，移动端 ≥44；颜色取自 `getComputedStyle`。
- R11 视觉确认：解绑后刷新，员工行显示“未绑定模型”（截图 `organization-unbound-1440x900.png`，已人工查看）。
- R4 视觉确认：模型中心用量区显示“完整计量 / 部分计量 / 未计量”，Token 未知显示“未知”而非 0（截图 `models-models-1440x900.png`，已人工查看）。

### 200% 缩放

- **等效布局门禁：通过。** CSS 视口 720×450、DPR 2，五个页面与抽屉均无横向溢出、最小字号 12px、抽屉落在视口内。证据 `zoom-200-equivalent.json`（内含 `explicitly_not`：这是等效，不是真实缩放）。
- **真实浏览器缩放：仍未通过。** 自动化 Chrome 窗口已置为前台（`foregroundMatch=true`），但页面内 `keydown` 监听器记录 **0** 个事件，DPR 保持 1，说明 OS 注入的按键到不了该渲染器。这是阶段 01 起就存在的环境阻塞，本轮未改变结论，也未用等效门禁冒充通过。

## 数据与安全

- 所有数据库操作只在隔离测试 PostgreSQL（`127.0.0.1:55434`）与隔离 Compose 栈执行。未连接或读取真实业务库、未修改 `.env`、未删除任何卷。
- 秘密边界加强：R1 修复后，供应商返回的任何字段都不可能把当前凭据写入数据库或回显到浏览器（含截断回显）；原有“canary 扫描全部响应与全部表”的测试仍通过。
- 调用证据加强：R2 之后，即使进程在调用中途消失，也留下 PENDING 意图行；R5 之后，供应商的畸形元数据不再导致 5xx 或抹掉证据。
- 审计不可绕过：每次探测（含被拒绝、失败、无效 JSON、PENDING）都写审计；审计仍无写入接口。
- 权限未变：所有新端点（含 `model-unbind`）要求所有者会话 + CSRF；`expectedVersion` 是并发控制，不是授权依据。
- 未新增任何可存密钥的列；新增列（版本、指纹、用量标志）均为非敏感结构信息。

## 模型验证

| 类别 | 结果 |
|---|---|
| 模拟传输（`httpx.MockTransport`） | 覆盖 R1 回显、R3 失效、R4 部分用量、R5 三种畸形输入、R7 并发表单、R9 清空；每个探测至多一次调用 |
| 真实本机 HTTP 协议模拟 | R6 用分块慢速服务验证总时限；另测同机固定开销作为对照组 |
| 真实供应商 API | **未执行**。无密钥，未访问 DeepSeek 或其他外部供应商；不声称验证过真实模型名、能力或价格 |
| 真实进程强杀 | **未执行**。R2 的“中断”用注入异常模拟，验证的是已提交的意图不会随请求事务回滚，不是操作系统层面的进程终止 |

## 未通过与未执行

- **真实浏览器 200% 缩放仍未验证**（环境阻塞，见上）。
- **真实供应商连通性未验证**（无密钥）。因此真实供应商是否会在 `model` 字段回显密钥、真实限流语义等只由模拟覆盖。
- **未执行**：真实进程强杀后的恢复、真实 TLS、跨标签页会话、压测、依赖漏洞扫描、屏幕阅读器实测。
- R6 的阈值依赖本机回环固定开销；该数值因机器而异，回归里用同机对照组而非绝对秒数。
- 降级路径在存在 `PENDING` 行时需要人工确认（旧约束不接受该值）。

## 验收判断

11 项发现全部修复，均有修复前失败与修复后通过的证据，另有 34 项端到端与 20 项浏览器检查通过；迁移可往返并含备份与验证步骤。

阶段 02 仍为**部分完成**：真实浏览器 200% 缩放与真实供应商连通性两项外部验证未关闭。这两项不因本轮修复而改变，也不构成本轮修复的遗留缺陷。阶段 03 未开始，未推送远程、未发布、未做生产部署。
