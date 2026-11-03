# 供应商密钥加密存储验收报告

日期：2026-11-03（Asia/Shanghai）
对应阶段：跨阶段变更（阶段 02 模型中心的密钥来源），ADR-0006
代码版本/分支：`main` 工作区（未提交、未推送）
环境及依赖版本：Python 3.13.12；PostgreSQL 17-alpine（本机 55432 测试库 + 隔离 Compose 栈 `acos-phase03-test`）；Node 22 / pnpm 10.34.9；新增 `cryptography==46.0.6`
本报告结论：**部分完成** — 功能、后端/前端测试、迁移往返与真实容器端到端路径已验证；截图未取得（工具超时），200% 缩放与真实 DeepSeek 调用未执行。

## 需求与证据

| 需求/验收 ID | 实际实现 | 测试方法与命令 | 实际结果 | 证据文件 |
|---|---|---|---|---|
| K1 界面直接填写密钥 | 供应商抽屉的"API 密钥"字段（`type=password`、`autocomplete=new-password`、打开时为空）提交 `secretValue`；服务端加密后写入 `providers.secret_ciphertext` | `pytest tests/test_provider_secrets.py`；浏览器 DOM 快照 + `getComputedStyle` | 15 项通过；真实容器中创建返回 `secretSource=DB`、`secretState=CONFIGURED`、`secretRef=null` | [evidence](evidence/provider-secret-storage/README.md) |
| K2 明文不落库、不回显 | AES-256-GCM 封套 `v1.<kid>.<nonce>.<ct>`；读取路径只给来源与状态 | canary 扫描全部表、全部 API 响应、日志；`psql` 直查 | 库中 `like '%sk-…%'` 命中 0；响应与 DOM 均无明文 | db-rows.txt |
| K3 缺少主密钥时拒绝保存 | `SecretCipher.from_setting(None)` → `encrypt` 抛错 → 503 `SERVICE_UNAVAILABLE`，不写库 | `test_key_requires_a_configured_server_encryption_key` | 503，`providers` 计数为 0 | — |
| K4 主密钥非法时启动失败 | 非 base64 或长度≠32 字节在构造期抛错 | `test_malformed_encryption_key_fails_at_startup` | 通过 | — |
| K5 更换主密钥后不静默成功 | `secretState=UNDECRYPTABLE`；探测本地拒绝 `SECRET_UNDECRYPTABLE`，`callCount=0`、不写用量、不结算预算 | `test_replaced_encryption_key_makes_the_probe_refuse_locally` | 通过；真实栈中同一主密钥下探测路径正常 | — |
| K6 留空不覆盖、显式清除才删除 | `changeSecretValue` 区分"省略"与"清空"；适配器对显式空值不回落环境变量 | `test_omitting_secret_value_on_update_preserves_the_stored_key`、`test_clearing_the_key_leaves_no_fallback_to_the_environment`；浏览器留空保存 | 密文与 version 均未变；清除后 `secretState=NONE`，环境变量同名也不被使用 | — |
| K7 换密钥作废旧能力证据 | 指纹的凭据分量由密文哈希构成 | `test_replacing_the_key_invalidates_probe_evidence`；真实栈 version 1→2、指纹变更 | 通过；`supportsJsonOutput` 回到未验证 | db-rows.txt |
| K8 旧环境变量供应商仍可用 | ENV 行按变量名解析；指纹输入与阶段 02 一致 | `test_legacy_env_provider_still_probes_from_the_environment`；既有 `test_organization.py` 全部通过 | 32 项通过 | — |
| K9 迁移可升级、降级有保护 | `a7d2e5c81b64` 纯增量；`downgrade` 在存在 DB 密钥时拒绝 | 真实库：`upgrade` → 插入 DB 行 → `downgrade`（拒绝）→ 清空 → `downgrade` → `upgrade` | 结果符合预期 | — |
| K10 界面状态区分 | `已配置（加密保存）` / `已配置（环境变量）` / `未配置` / `未配置（环境变量缺失）` / `无法解密` | `frontend/src/__tests__/provider-secret.spec.ts`（6 项）；浏览器快照 | 6 项通过；真实页面显示"已配置（加密保存）" | — |

## UI 检查

- 视口：1440×900 与 390×844 用真实浏览器（IAB）加载隔离栈页面。
- 390×844：抽屉宽 390 未溢出视口，`document.scrollWidth == 390`（无横向滚动）；密钥输入高 44px（满足触达下限），字号 16px（避免移动端自动缩放）。
- 1440×900：标签文字对比 17.85:1，控件边框对背景 4.76:1，均满足 4.5:1 / 3:1 要求。
- 关闭态不渲染密钥框：抽屉关闭时 `input[type=password]` 计数为 0。
- 键盘：未单独重测（本次改动未新增自定义键序；表单沿用既有 AppInput / FormField）。
- **截图未取得**：`tab.screenshot()` 连续两次 30 秒超时（该限制在先前会话中已记录）。因此本项**不满足** `quality-gates.md` 的"三视口截图"要求，按未完成记录。
- 200% 缩放未执行（此前已确认本机无法真实模拟）。

## 数据与安全

- 迁移 `a7d2e5c81b64`：新增可空列与三条 CHECK；既有 ENV 行不改写；降级在有密钥时拒绝，避免删除唯一副本。
- 凭据来源唯一性由 `secret_source_single` 与 `secret_ciphertext_requires_db` 两条约束保证，数据库层拒绝"两种来源并存"。
- 指纹输入：ENV 行为变量名（与阶段 02 字节一致，历史指纹仍可复现）；DB 行为 `db:<sha256(密文)[:16]>`，哈希对象是密文而非明文。
- 并发：沿用阶段 03 的乐观锁与行锁；新增的凭据写入发生在同一事务内，加密失败时整笔回滚，已存密钥不受影响。
- 权限：所有写入仍要求所有者会话 + CSRF；无新增读密钥端点。
- 日志/审计：仅记录 `secret_source` 与 `secret_configured`；审计读取接口对含 `secret` 的 detail 继续二次脱敏。

### 顺带修复的既有问题（与本需求无关但阻断回归）

`backend/app/db/session.py` 的 `connect` 事件执行 `SET TIME ZONE 'UTC'` 时隐式开启了事务，导致连接池中的连接停留 `INTRANS`，之后任何以 `AUTOCOMMIT` 取用该连接的代码都会失败。阶段 03 的 `test_step_start_and_recovery_do_not_deadlock` 因此稳定失败（14 项中 1 项）。修复方式是在 `connect` 事件内临时打开 autocommit 执行该语句后恢复。修复后该文件 14 项全部通过，全量 258 项通过。此修复未改变生产行为语义，只是让连接在池中保持空闲状态。

## 模型验证

- 演练：未涉及。
- 模拟 API：`httpx.MockTransport` 覆盖成功、认证失败、限流、超时、无效 JSON、密钥回显清洗等路径（`test_provider_secrets.py`、既有 `test_provider_adapter.py`）。
- 真实 API：**未执行**。没有可用的 DeepSeek 密钥，且本轮不允许把真实密钥写入仓库或测试库。

## 未通过与未执行

| 项 | 原因 | 影响 | 下一步 |
|---|---|---|---|
| UI 截图 | IAB 截图 30s 超时 | 视觉验收缺截图证据 | 在有可用截图环境时补 1440/1024/390 三视口 |
| 200% 缩放 | 本机不可真实模拟 | 缩放可用性未验证 | 需要在可调真实缩放的浏览器上执行 |
| 真实 DeepSeek 调用 | 无密钥 | 真实协议能力未验证 | 用户提供密钥后在模型中心填写并测试连接 |
| 密钥批量轮换工具 | 超出本次范围 | 更换主密钥需逐条重填 | 如需要，后续实现重加密命令 |

## 验收判断

功能层面可通过：界面可直接填写密钥、加密入库、不回显、旧配置兼容、换密钥有明确且非静默的失败、迁移可往返且降级有保护。  
证据层面为**部分完成**：截图与真实供应商调用缺失，按 `quality-gates.md` 不能宣称 UI 门禁全部通过。下一阶段可以继续，但上述两项应在发布前补齐。
