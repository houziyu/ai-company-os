# 阶段 02 证据索引

日期：2026-10-30。所有文件均为本轮实际执行产生，未手工编辑结果。

## 检查命令输出

| 文件 | 内容 | 结果 |
|---|---|---|
| `static-checks.txt` | 后端 `ruff check`、`ruff format --check`、`mypy app` | 全部通过 |
| `backend-pytest.txt` | 后端完整 pytest（真实 PostgreSQL） | 141 passed，4 个既有依赖弃用警告 |
| `frontend-checks.txt` | 前端 `pnpm check`（tokens/lint/typecheck/test/build） | 83 passed，构建通过 |
| `migration-path.txt` | 迁移 01 → 02 → 降级 → 再升级的实测表数与模板种子 | 7 → 19 → 7 → 19 表；5 行模板；未在迁移中创建公司 |
| `e2e-compose.txt` | 对运行中的隔离 Compose 栈执行 `scripts/verify_phase02.py` | 31 项检查全部通过 |
| `ui-checks.txt` | 浏览器检查 `scripts/verify_phase02_ui.cjs`（真实栈） | 18 项检查通过 |
| `ui-results.json` | 上述浏览器检查的结构化结果 | — |
| `zoom-200-equivalent.txt` / `zoom-200-equivalent.json` | 200% 缩放等效布局门禁（CDP 设备指标：CSS 视口减半 + DPR 翻倍） | 4 页面 + 抽屉通过 |
| `zoom-200-real-attempt.txt` / `zoom-200-real.json` | 真实浏览器缩放的实机尝试与诊断 | **未通过 / 未验证**，原因见文件内 `conclusion` 与 `evidenceForBlock` |

## 截图（真实栈，非模拟数据）

均通过 `scripts/verify_phase02_ui.cjs` 在隔离 Compose 栈上生成。该栈的供应商密钥为空，因此模型中心截图显示“未配置”——这是该环境的真实状态，不是模拟成功。

| 视口 | 页面 |
|---|---|
| 1440×900 | `setup-1440x900.png`、`wizard-step1..4-1440x900.png`、`overview-1440x900.png`、`organization-1440x900.png`、`organization-drawer-1440x900.png`、`organization-renamed-1440x900.png`、`models-empty-1440x900.png`、`models-provider-form-1440x900.png`、`models-providers-1440x900.png`、`models-models-1440x900.png`、`models-assignments-1440x900.png`、`models-assignments-bound-1440x900.png`、`audit-1440x900.png`、`audit-filtered-1440x900.png`、`settings-dialog-1440x900.png` |
| 1024×768 | `overview-1024x768.png`、`organization-1024x768.png`、`models-1024x768.png`、`audit-1024x768.png` |
| 390×844 | `overview-390x844.png`、`organization-390x844.png`、`models-390x844.png`、`audit-390x844.png`、`mobile-nav-390x844.png` |
| 360×800 | `overview-360x800.png`、`organization-360x800.png`、`models-360x800.png`、`audit-360x800.png`、`mobile-nav-360x800.png` |
| 200% 等效 | `zoom200equiv-overview.png`、`zoom200equiv-organization.png`、`zoom200equiv-models.png`、`zoom200equiv-audit.png`、`zoom200equiv-setup-company.png`、`zoom200equiv-drawer.png` |

## 复现命令

从仓库根目录，先按 README 生成 `.env` 并启动隔离栈：

```powershell
./scripts/phase02_test_stack.ps1 -ComposeArgs @('up','-d','--build')
./backend/.venv/Scripts/python.exe scripts/verify_phase02.py --base-url http://127.0.0.1:18083 --compose-project acos-phase02-test
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_ui.cjs
node scripts/verify_phase02_zoom200.cjs --method=equiv
node scripts/verify_phase02_zoom200.cjs --method=os-input
./scripts/phase02_test_stack.ps1 -ComposeArgs @('stop')
```

`verify_phase02.py` 只在全新栈上运行（它会创建所有者）。重跑前必须重建该项目的数据库卷：

```powershell
docker compose --project-directory . --env-file .env.example -p acos-phase02-test `
  -f docker-compose.yml -f deploy/compose.phase02-test.yml down
docker volume rm acos-phase02-test_postgres_data
```

`--method=os-input` 在当前主机无法通过，属已知阻塞；`--method=equiv` 是等效布局门禁，不能替代真实缩放。
