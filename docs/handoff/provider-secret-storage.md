# 供应商密钥加密存储 — 交接

日期：2026-11-03（Asia/Shanghai）。[验收](../acceptance/provider-secret-storage.md)｜[ADR-0006](../decisions/0006-provider-secret-storage.md)｜[证据索引](../acceptance/evidence/provider-secret-storage/README.md)

## 本轮范围

**已完成**：模型中心改为直接填写 API 密钥；服务端 AES-256-GCM 加密保存；旧的
环境变量引用保持可用；界面与接口均不回显已保存密钥；状态区分"未配置 / 环境变量
缺失 / 无法解密 / 已配置"；迁移 `a7d2e5c81b64` 及其降级保护；后端与前端回归；
隔离 Compose 栈端到端验证。

**未完成**：三视口截图（截图工具超时）、200% 缩放、真实 DeepSeek 调用、密钥批量
轮换工具（见验收"未通过与未执行"）。

**明确不在本轮范围**：阶段 04 隔离执行器；把主密钥迁到 KMS/Vault；发版、推送、
生产部署。

## 工程入口

| 路径 | 作用 |
|---|---|
| `backend/app/security/secrets_cipher.py` | AES-GCM 封套、`kid`、加解密与"能否解密"判定（新增） |
| `backend/app/services/model_centre_service.py` | 凭据校验/加密/清除、指纹凭据分量、`secret_state`、探测前解密与本地拒绝 |
| `backend/app/providers/adapter.py` | `secret_value` 优先且不回落环境变量（`resolve_credential`） |
| `backend/app/models/organization.py` | `secret_ciphertext` 列与三条来源约束 |
| `backend/alembic/versions/a7d2e5c81b64_provider_secret_storage.py` | 迁移与拒绝式降级（新增） |
| `backend/app/api/schemas_organization.py` | `secretValue` / `changeSecretValue` / `secretSource` / `secretState` |
| `backend/app/api/v1/model_centre.py` | 响应组装与状态计算 |
| `frontend/src/views/ModelsView.vue` | 密码输入、留空不改、显式清除、旧配置只读说明、状态文案 |
| `backend/tests/test_provider_secrets.py` | 后端回归，15 项（新增） |
| `frontend/src/__tests__/provider-secret.spec.ts` | 前端回归，6 项（新增） |
| `backend/app/db/session.py` | 顺带修复：连接初始化语句不得在池中留下未结束事务 |

## 实际可用的检查命令

```powershell
# 一次性 PostgreSQL（不要指向真实数据库）
docker run -d --name acos-test-pg -e POSTGRES_USER=acos -e POSTGRES_PASSWORD=devpass `
  -e POSTGRES_DB=acos -p 55432:5432 postgres:17-alpine

cd backend
./.venv/Scripts/python.exe -m pytest -q                       # 258 passed
./.venv/Scripts/python.exe -m ruff check app tests            # All checks passed
./.venv/Scripts/python.exe -m ruff format --check app tests   # 需排除未跟踪的 scripts_create_owner.py
./.venv/Scripts/python.exe -m mypy app                        # Success

cd ../frontend
corepack pnpm check                                           # lint + typecheck + 116 tests + build
```

实跑结果（2026-11-03）：后端 258 passed；前端 116 passed；`ruff`、`mypy`、`vite build` 均通过。

## 部署与升级注意

1. **必须配置 `SECRET_ENCRYPTION_KEY`**（32 字节随机值的 base64）。缺失时模型中心保存密钥返回 503，不会明文入库。`scripts/generate_secrets.py` 已自动生成该值；已有 `.env` 需手工补一行。
2. 该值与数据库备份**分开保管**。丢失或更换后，已保存的密钥无法解密，界面显示"无法解密"，需要逐条重新填写。
3. 升级：`docker compose up -d --build`（后端镜像需重建以安装 `cryptography`）。
4. 降级到 `a7d2e5c81b64` 之前：若已有加密密钥，迁移会**拒绝**降级。先在模型中心清除这些密钥（或 `UPDATE providers SET secret_ciphertext=NULL, secret_source='ENV', secret_ref=NULL`），再执行降级。
5. 旧的 `DEEPSEEK_API_KEY` 仅对仍引用环境变量的供应商行有效；新配置无需它。

## 已知风险

- **主密钥丢失不可恢复**：这是本设计的直接代价，已在 README、ADR 与错误文案中明确写出，未做任何"看似可用"的兜底。
- **数据库备份含义变化**：备份含密文，需与主密钥分开保管。
- **无批量轮换**：更换主密钥后需逐条重填；`kid` 字段已为将来的多密钥轮换预留。
- **截图门禁未过**：本轮 UI 证据来自 DOM 快照与计算样式，不是截图；发布前应补。
- 本机 IAB 的 `tab.screenshot()` 连续超时，属于环境限制，不是页面缺陷。

## 与既有约定的关系

- `AGENTS.md` 的密钥条目已改写为"服务端加密保存、界面不回显"。
- ADR-0003 的密钥条目已标注由 ADR-0006 取代，正文保留为历史记录。
- 阶段 02 的历史证据脚本（`docs/acceptance/evidence/phase-02-*`）**未改动**，它们记录的是当时的契约。
- `scripts/verify_phase02*.py|cjs`、`verify_phase03_*.py` 与两个测试栈脚本已按新契约更新（新增主密钥、状态断言、密码框断言）。
