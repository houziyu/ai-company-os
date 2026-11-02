# 阶段 02 第二版复审修复验收

日期：2026-11-02（Asia/Shanghai）。对应阶段：02，复审 V2-1～V2-5 修复。
代码版本/分支：工作目录无 `.git`；修改前与 2026-10-30 复审源码 SHA-256 一致。变更文件指纹见[证据索引](evidence/phase-02-v2-fixes/README.md)。
环境：Windows、仓库既有 Python/Node/pnpm 及锁定依赖，独立 PostgreSQL `127.0.0.1:55434`。浏览器版本见 `ui-results.json`。没有升级依赖。

结论：**本轮 5 项修复及回归通过；阶段 02 仍为部分完成。** 真实浏览器 200% 缩放、真实供应商连通性、真实进程强杀恢复继续标为未验证。本轮不进入阶段 03。

## 需求与证据

修改前核对源码指纹一致，再原样复跑上次独立探针：后端 **4 failed**、前端 **1 failed**。原复审目录保持原样，本轮输出另存 `backend-before.txt`、`frontend-before.txt`。

| 需求 | 实现 | 实测结果 | 证据 |
|---|---|---|---|
| V2-1 旧探测覆盖 | `_finish_row` 按 provider→model 顺序加 `FOR NO KEY UPDATE` 锁并 `populate_existing`，用当前指纹决定是否更新能力；统一模型列表、员工分配、组织引用的能力有效性 | 旧成功晚于新失败到达后，数据库与各读取出口均保持新配置的 `false / FAILURE`；两次用量和探测均保留 | `v2-regressions-final.txt` 的 `late-old-probe`；正式测试 `test_v2_fixes.py` |
| V2-2 历史迁移指纹 | 新增数据迁移 `8c24a71d9e03`；回填规范指纹，清除归属缺失或不匹配的当前能力，保留有效能力及历史 | 从 `7b41c9d2f0aa`、`3f8d2c6a91b4` 两种起点升级：旧能力 null，重测 SUCCESS 后能力 true、stale false；含 PENDING 的升级→降级→升级历史不变 | 同上 `migration-evidence`、`migration-preservation` |
| V2-3 总时限 | 同步 `probe()` 保留；内部使用 `AsyncClient` 和 `asyncio.timeout` 取消网络操作，新增可直接 await 的 `probe_async()`；TLS 信任库在启动时加载一次 | 1 秒预算：响应头滴流 **1.007 秒**，正文耗尽剩余预算 **1.025 秒**；服务端均观测到 EOF，正文测试确认只调用一次 | 同上 `slow-headers`、`body-stall` |
| V2-4 Unicode | 模型标识写库前验证 UTF-8 可编码性；非法值丢弃，失败响应同样传递 metadata_dropped | 高/低孤立代理码点 × 有效/无效 JSON，4 种组合均 200，分别留下 1 条 probe、usage、审计；21/7 Token 保留，resolvedModelName null | 同上 `surrogate-model` |
| V2-5 部分保存恢复 | PATCH 成功立即回填员工对象/版本；刷新返回明确成功状态，失败不重置草稿 | 组织刷新或模型中心刷新失败两种情况均提交 `[1,2]`，保留改名与模型选择；浏览器四视口复现通过 | `v2-fixes.spec.ts`、`frontend-checks-verified.txt`、`ui-results.json` |
| 验收发现的手机抽屉问题 | 修复 AppDrawer 的 Teleport 根元素响应式规则；移动端在组件根上将现有宽度 token 映射为 100%，无新增 token/颜色/尺寸，无 `!important` | 修复前 390px 视口抽屉左边界 -170；修复后 390/360px 左边界 0，右边界等于视口，提示不再截断 | `ui-diagnostic.txt`、`debug-partial-390x844.png`、最终 `partial-save-*.png` |

### 检查命令

| 命令（对应目录） | 结果 |
|---|---|
| 后端 `python -m ruff check .` | 通过 |
| 后端 `python -m ruff format --check .` | 60 files already formatted |
| 后端 `python -m mypy app` | 43 source files 通过 |
| 后端 `python -m pytest tests/test_v2_fixes.py -q -s --tb=short` | **11 passed**，8 个依赖弃用警告 |
| 后端 `python -m pytest -q` | **170 passed**（原 159 + 新 11），10 个依赖弃用警告，192.30 秒 |
| 前端 `pnpm check` | tokens/lint/typecheck/**93 passed**（原 91 + 新 2）/build 全通过 |
| 根目录 `node scripts/verify_phase02_v2_ui.cjs` | 4 个视口的失败恢复、键盘提交、重试版本、重新读取通过；无 pageerror |

首次增补的“服务端写入抛错即连接关闭”断言受 Windows TCP 缓冲影响，不能可靠观测关闭；已改为直接检查服务端 socket EOF，仍断言实际关闭，未改宽超时阈值。前端首次完整检查发现临时 console 输出和局部宽度变量不符合现有门禁，已移除输出并改用既有宽度 token。最终输出见 `*-verified.txt`/`v2-regressions-final.txt`，中间失败日志保留。

### 测试迁移说明

原始独立探针未改。正式测试保留正确行为断言并扩展；两项夹具适配有必要说明：

- 网络传输注入现在要求 `httpx.AsyncBaseTransport`；真实回环探针由 `HTTPTransport` 改为 `AsyncHTTPTransport`。MockTransport 同时支持两种接口，既有模拟测试继续使用它。未放宽 1.5 秒的总时间断言。
- 配置竞态由同步回调内嵌另一次探测，改为独立线程与事件同步的两个事务，避免在异步回调中嵌套运行同步事件循环。交错顺序、当前状态和历史保留断言保留并扩展到全部读取出口。

## UI 检查

运行的是**构建后的真实 SPA + 明确标注的内存模拟 API**，不是 Compose 端到端；模拟 API 用于稳定重现“PATCH 成功、绑定失败、随后 GET 503”。页面公司名、错误文案、报告均标明模拟。

视口 1440×900、1024×768、390×844、360×800；每个视口保存组织默认页和部分保存错误状态。四张 `partial-save-*.png` 已人工查看：提示可读、姓名保留、底部操作可见，抽屉内部滚动，手机无左右裁切。脚本用 Enter 打开员工并提交，用 Escape 关闭；重试后观测版本 `[1,2]`，恢复读取后姓名仍在。手机抽屉修复前截图保留作对照。

没有重新宣称整站颜色/组件尺寸验收、真实浏览器缩放或完整登录键盘路径通过。公共抽屉只修改移动宽度映射，既有组件与 token 测试全部通过。

## 数据与安全

数据库只使用专用容器 `acos-phase02-review-20261030`，确认绑定 `127.0.0.1:55434`，每项测试创建随机克隆库。fixture 只移除自己创建的库；没有读写 `.env`，没有修改既有 Compose 栈或删除其卷。测试结束专用容器恢复停止状态。

锁顺序与供应商编辑一致；使用 NO KEY UPDATE 是因为这些操作不改主键，并避免与探测插入的外键 KEY SHARE 锁冲突。配置比较和能力写入在同一锁保护的事务里，网络等待期间不持有这些配置锁。

新增迁移只更新 `providers.config_fingerprint` 及不可信的模型当前能力字段；不改身份、绑定、探测历史、用量、审计。有效指纹对应的能力保留。降级到 `3f8d2c6a91b4` 为数据保留的 no-op，**不会恢复错误能力或空指纹**；进一步降级仍受旧迁移的 PENDING 限制。应在线执行此数据迁移，不使用 `--sql` 离线生成模式。

### 升级前备份与验证（部署操作说明，本轮未执行生产操作）

先停止写入并等在途探测结束；记录应用版本和 UTC 时间。以下在仓库根目录 PowerShell 使用实际部署配置，禁止 `down -v`：

```powershell
docker compose stop web api worker
New-Item -ItemType Directory -Force backups | Out-Null
$backupName = 'phase02-pre-v2-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '.dump'
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/phase02-pre-v2.dump'
docker compose cp postgres:/tmp/phase02-pre-v2.dump "backups/$backupName"
Get-FileHash -Algorithm SHA256 "backups/$backupName"
docker compose exec -T postgres sh -c 'pg_restore --list /tmp/phase02-pre-v2.dump >/dev/null'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"'
docker compose build migrate api worker web
docker compose run --rm migrate
# 上一步退出码必须为 0；预期迁移头 8c24a71d9e03。
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"'
docker compose up -d
Invoke-RestMethod http://127.0.0.1:8080/api/v1/health/ready
```

保留备份文件、校验和、原版本号。`pg_restore --list` 只检查备份可读取，不等于真实恢复演练。升级后旧未知归属能力应变为“未验证”，历史探测仍在；重新测试连接属于一次可能付费的操作，需按正常产品流程执行。本轮仅以模拟供应商完成上述行为验证。

## 模型验证

- MockTransport：配置竞态、Unicode、用量、失败、恢复，全部为模拟，不访问外部供应商。
- 真实回环 HTTP：响应头滴流、正文停滞，观测总时间和客户端连接关闭；每次至多一次调用。
- 浏览器 API：明确模拟状态，验证前端恢复，不替代后端持久化测试。
- 真实供应商 API：**0 次，未执行**。

## 未通过与未执行

最终针对性及全量检查均通过。本轮未执行完整 Compose 端到端、真实 TLS、真实进程强杀恢复、真实浏览器 200% 缩放、真实供应商和生产备份恢复。旧的外部验证缺口继续保留，不以模拟替代。

## 验收判断

V2-1～V2-5 均有本轮修复前失败和修复后通过的证据；新增迁移覆盖两种升级起点及包含 PENDING 的往返，未删除历史。额外手机抽屉问题已通过实际截图关闭。可交接本轮修复；**阶段 02 整体仍为部分完成，不宣称阶段 03 门禁完成**。未推送、发布或生产部署。
