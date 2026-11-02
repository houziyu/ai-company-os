# 第二版复审修复证据

日期：2026-11-02。报告：[修复验收](../../phase-02-v2-fixes.md)。原 [复审证据](../phase-02-recheck/README.md) 保持不变。

| 文件 | 用途 |
|---|---|
| `backend-before.txt` / `frontend-before.txt` | 原样复跑旧探针，修复前 4+1 项失败 |
| `backend-targeted.txt` | 首轮适配器、前轮修复及新修复的联合回归，69 passed |
| `v2-regressions-final.txt` | 最终新增后端 11 项通过，含结构化观测、EOF 关闭、迁移往返 |
| `backend-full.txt` | 最终后端全量 170 passed，10 warnings |
| `frontend-checks-verified.txt` | 最终 pnpm check：93 passed / 11 files，全部检查通过 |
| `ui-checks-verified.txt` / `ui-results.json` | 最终四视口浏览器结果，模拟 API，版本 [1,2]，无页面异常 |
| `organization-*.png` / `partial-save-*.png` | 四视口默认组织、部分保存错误状态；后者四张已人工检查 |
| `ui-diagnostic.txt` / `debug-partial-390x844.png` | 验收发现手机抽屉宽度错误，修复前对照 |
| `checks-summary.json` / `changed-source-sha256.json` | 实测检查摘要、修改前后源码指纹；不含秘密 |

其他 `frontend-checks*.txt`、`ui-checks*.txt`、`v2-regressions.txt` 为修复过程中的中间输出，不能当最终结果。最终结果以表中明确标识的文件为准。

## 复现

后端从 `backend`，仅使用隔离库：

```powershell
$env:TEST_ADMIN_DATABASE_URL='<隔离 PostgreSQL 管理连接串>'
$env:PYTHONIOENCODING='utf-8'
./.venv/Scripts/python.exe -m pytest tests/test_v2_fixes.py -q -s --tb=short
./.venv/Scripts/python.exe -m pytest -q
```

前端从 `frontend` 执行 `pnpm check`；浏览器从根目录执行：

```powershell
$env:NODE_PATH='C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/verify_phase02_v2_ui.cjs
```

浏览器脚本只监听 `127.0.0.1:18084` 并运行内存模拟 API，不读取 `.env`、不访问业务数据库，也不需要删除数据卷。后端迁移试验只针对测试 fixture 新建的随机克隆库。
