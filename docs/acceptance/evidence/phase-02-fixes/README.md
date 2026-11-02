# 阶段 02 修复证据索引（独立质检 R1–R11）

日期：2026-10-30。原始质检报告与它的证据保持原样：
[docs/acceptance/phase-02-review.md](../../phase-02-review.md)、
[docs/acceptance/evidence/phase-02-review/](../phase-02-review/README.md)。

## 修复前后对照

| 文件 | 内容 |
|---|---|
| `probe-before.txt` | **修复前**：原质检探针在本工作区的复跑，11 项失败（与质检报告一致） |
| `frontend-probes-before.txt` | **修复前**：原前端探针复跑，3 项失败（R10 选中/焦点 1/0、R11 绑定请求 0、R8 版本 [1,1] 而服务端为 2） |
| `probe-after.txt` | **修复后**：同一批缺陷的正式回归（`backend/tests/test_review_fixes.py`，18 项）全部通过，含结构化观测 |

## 检查命令输出

| 文件 | 内容 | 结果 |
|---|---|---|
| `static-checks.txt` | 后端 `ruff check`、`ruff format --check`、`mypy app` | 全部通过；59 文件格式、42 源文件类型 |
| `backend-pytest.txt` | 后端完整 pytest（真实 PostgreSQL） | **159 passed**，4 个既有依赖弃用警告 |
| `frontend-checks.txt` | 前端 `pnpm check`（tokens/lint/typecheck/test/build） | **91 passed / 10 文件**，构建通过 |
| `migration-path.txt` | 迁移 7b41c9d2f0aa → 3f8d2c6a91b4 → 降级 → 再升级；旧数据回填 | 见下节 |
| `e2e-compose.txt` | 隔离 Compose 栈上 `scripts/verify_phase02.py` | **34 项全部通过** |
| `ui-checks.txt` | 浏览器检查 `scripts/verify_phase02_ui.cjs` | **20 项通过** |
| `zoom-200-equivalent.txt` | 200% 缩放等效布局门禁 | 通过（**不是**真实缩放） |
| `zoom-200-real-attempt.txt` | 真实浏览器缩放尝试 | **未通过**，原因见 `../phase-02/zoom-200-real.json` |

## 迁移实测（`migration-path.txt`）

- 修复前 schema：19 表，`outcome` 约束只允许 `SUCCESS/FAILURE`，无 per-dimension 用量列。
- 修复后：19 表（无新增表，只加列与约束），`outcome` 允许 `PENDING/SUCCESS/FAILURE`，新增 `prompt_tokens_known`/`completion_tokens_known`。
- 旧数据回填：一条 `prompt_tokens=21, completion_tokens=NULL, usage_known=true` 的历史行在升级后被改为 `usage_known=false, prompt_tokens_known=true, completion_tokens_known=false`——旧的“部分已知被当成完整计量”不再延续。
- `providers.version` 默认 1；`providers.config_fingerprint` 对历史行为空串（与任何真实指纹都不相等，因此旧证据被如实视为过期）。
- 迁移**不**为历史探测捏造 PENDING 行：升级后 `PENDING` 行数为 0。
- 降级回 `7b41c9d2f0aa` 只删除新增列与放宽的约束，表数保持 19；再升级可重复。

## 截图

均在隔离 Compose 栈（真实 API + PostgreSQL，无供应商密钥）上生成，与阶段 02 原截图同一目录 `../phase-02/`：

- `organization-unbound-1440x900.png`：R11 解绑后刷新仍为“未绑定模型”。
- `models-models-1440x900.png`：R4 用量面板显示“完整计量 / 部分计量 / 未计量”，Token 未知显示“未知”。
- 其余视口截图（1440×900 / 1024×768 / 390×844 / 360×800）由同一脚本重跑覆盖。

## 复现

```powershell
# 后端（独立测试 PostgreSQL，不要指向业务库）
cd backend
$env:TEST_ADMIN_DATABASE_URL='<隔离测试实例管理连接串>'
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pytest tests/test_review_fixes.py -q -s   # 带 REVIEW 观测行

# 前端
cd ../frontend
pnpm check

# 隔离栈端到端与浏览器
./scripts/phase02_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_ui.cjs
node scripts/verify_phase02_zoom200.cjs --method=equiv
```

`verify_phase02.py` 只在全新栈上运行；重跑前删除 `acos-phase02-test_postgres_data` 卷。
