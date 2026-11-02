# 阶段 02 第二版独立复审

日期：2026-10-30（Asia/Shanghai）。对应阶段：02，R1–R11 修复后的当前代码快照。当前目录无 `.git`，不能提供提交差异；源码 SHA-256 见[证据索引](evidence/phase-02-recheck/README.md)。

本报告结论：**失败，发现 5 项 P2 正确性问题，均有独立失败探针。不能确认“R1–R11 已全部关闭，只有外部验证未完成”。** 本轮只审查，不修复应用、不进入阶段 03。

环境：现有 Windows 开发环境、仓库锁定依赖、独立 PostgreSQL 容器 `acos-phase02-review-20261030`，已核对仅绑定 `127.0.0.1:55434`。测试 fixture 创建随机模板库和克隆库；没有连接业务库。

## 需求与证据

| 检查 | 实际方法/命令 | 实际结果 | 证据 |
|---|---|---|---|
| 后端静态检查 | `ruff check .`、`ruff format --check .`、`mypy app` | 全通过，57 个格式文件、42 个类型检查源文件 | [检查摘要](evidence/phase-02-recheck/checks-summary.json) |
| 前端现有检查 | `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm exec vite build` | 全通过，91 passed / 10 files | 同上（实测输出摘要） |
| 后端现有回归 | 独立 PostgreSQL，`python -m pytest -q` | 159 passed，4 warnings，183.53 秒 | [完整回归](evidence/phase-02-recheck/backend-baseline.txt) |
| 配置竞态、迁移、异常元数据 | 真实 PostgreSQL + API/应用服务 + 模拟供应商 | 3 项正确行为断言失败 | [探针](evidence/phase-02-recheck/test_recheck_probes.py)、[原始输出](evidence/phase-02-recheck/backend-probes.txt) |
| 总超时 | 真实回环 HTTP 服务缓慢发送响应头 | 1 秒预算耗时 3.738 秒，断言失败 | [超时输出](evidence/phase-02-recheck/timeout-probe.txt) |
| 部分保存与刷新失败 | 真实 Vue 组件/API 客户端，替换 fetch 响应 | 提交版本 `[1,1]`，断言失败 | [前端探针](evidence/phase-02-recheck/frontend-probe.spec.ts)、[输出](evidence/phase-02-recheck/frontend-probe.txt) |

以下编号为本轮独立发现，不替换原 R1–R11 编号。

### V2-1 · P2：迟到旧探测仍能覆盖新配置的探测结果

位置：`backend/app/services/model_centre_service.py:951–957`；关联读取：`backend/app/api/v1/model_centre.py` 的员工分配响应、`backend/app/api/v1/organization.py:_model_ref`。

`_finish_row` 所谓的 `current_fp` 来自网络调用前加载的 `provider` ORM 对象。外部调用期间另一个事务更新配置后，此对象不会自动刷新，因此与探测自己的旧指纹比较总会通过；没有锁内重读或带配置条件的原子更新。

实测顺序：A 配置发起探测 → 独立事务将地址改为 B 并提交 → B 的新探测返回无效 JSON、记录 FAILURE → A 的旧成功结果最后返回。最终数据库被改回 `supports_json_output=true / last_probe_outcome=SUCCESS`。模型列表虽通过指纹不匹配遮蔽为 `null`，员工分配接口却返回 `jsonVerified=true`，会显示“JSON 已验证”，新配置实际失败的结果被覆盖。历史两条探测仍在，本问题是当前状态归属错误。

建议：结果落库时，在统一锁顺序下重新读取当前供应商/模型配置并进行条件更新；旧探测只写自己的历史和用量。所有能力展示出口复用同一有效性判断。新增交错事务测试，不能只测“探测完成后再改地址”。

### V2-2 · P2：迁移后旧验证被信任，重新成功探测反而被标为过期

位置：`backend/alembic/versions/3f8d2c6a91b4_phase_02_review_fixes.py:56–69`、`backend/app/api/v1/model_centre.py:91–95`、`model_centre_service.py:951–966`。

迁移给旧 provider 留空串指纹，给旧 model 留 NULL 探测指纹，并保留既有能力值。读取时 `bool(model.probe_config_fingerprint and ...)` 将 NULL 判为“不陈旧”，所以迁移文档宣称的“旧证据一律过期”没有发生。重新探测时服务计算真实指纹并只写到 model，provider 的空串仍未回填；此后每次成功探测的 model 指纹都与 provider 空串不等。

实测在带成功探测的隔离克隆库上降到旧 schema 再升级：升级后 `supportsJsonOutput=true / probeEvidenceStale=false`；重测接口返回 SUCCESS 后，模型中心变成 `supportsJsonOutput=null / probeEvidenceStale=true`。用户按提示反复重测也无法通过，除非另行修改供应商配置触发指纹写回。

建议：为历史供应商建立一致的规范指纹，明确失效未知归属的旧能力；读写双方对空/缺失指纹采用相同规则。迁移回归必须包含已有成功探测的配置，以及升级后的首次成功重测。

### V2-3 · P2：缓慢响应头仍绕过总截止时间

位置：`backend/app/providers/adapter.py:384–396`，以及 `_read_capped_body:324–326`。

`client.stream().__enter__` 要先读完响应头才返回；目前截止检查只发生在正文块产出之后。供应商可以以短于 read timeout 的间隔持续发响应头，使调用超出总预算。即使进入正文循环，每次阻塞读也没有按剩余总时间缩短超时。

实测本机服务每 0.3 秒发一个响应头，共 12 个，配置 1 秒，直到 3.738 秒才返回 TIMEOUT。测试把真实 `HTTPTransport` 的初始化放在计时外，排除了修复报告所说的客户端固定建立开销；仍未在截止时终止 I/O。当前“固定开销 + 预算 + 余量”的回归只覆盖正文滴流，不能证明总时限。

建议：为完整网络调用实施可取消的总截止时间，覆盖响应头、每次阻塞读取及解析；到期关闭外部 I/O。增加响应头滴流和临近截止时阻塞读的测试，不只检查最终错误码为 TIMEOUT。

### V2-4 · P2：模型名中的非法 Unicode 仍使已知用量与审计回滚

位置：`backend/app/providers/adapter.py:245–251`。

筛查仅检查长度与 ASCII 控制字符，未拒绝孤立代理码点。供应商在合法 JSON 转义字符串中返回 `model="\ud800"` 时，Python JSON 解析得到字符串并通过筛查，但 psycopg 写入 PostgreSQL 时抛出 `UnicodeEncodeError`。

实测其余响应正常，含输入 21 / 输出 7 Token：请求触发 500（TestClient 重新抛出 UnicodeEncodeError），数据库只剩已提交 PENDING 意图，`provider_usage_records=0`、本次探测审计=0。R2 的意图保留确实生效，因此不是“全部调用证据消失”；但供应商已报告用量和最终结果仍被异常字段丢弃，R5 的外部元数据边界尚未封闭。

建议：落库前检查文本可被目标 UTF-8 编码，非法模型标识应丢弃并保留结果、用量、审计；增加孤立高/低代理码点测试。不要把此输入误当数据库不可用。

### V2-5 · P2：部分保存后的刷新失败仍恢复旧版本并覆盖草稿

位置：`frontend/src/views/OrganizationView.vue:269–277,318–323`。

资料 PATCH 响应里的 `updated` 没有合并到本地员工，只把版本存入提示用标量 `profileSavedVersion`。第二步后 `load()` 同时刷新组织和模型中心，只要任一请求失败就吞掉错误并保留旧 organization；调用者仍从旧 agents 找人并执行 `resetDraft`。这与“第一步后立即回填版本”的交接描述不符。

实测：资料改名成功，版本 1→2；绑定返回 409；随后组织读取成功但模型中心返回 503。第一次保存后姓名输入退回 `review-agent`；再次保存仍发送版本 1，实际提交版本为 `[1,1]`，服务端仍为 2。新增测试只改变刷新失败条件，原先全部读取成功的回归保持通过。

建议：第一步响应成功后立即更新本地员工实体和并发版本，不依赖后续 GET；让刷新显式报告成功/失败，只在取得新数据时重置已保存字段，并保留未保存绑定草稿。

## UI 检查

本轮没有修改页面或公共组件。实测 Vue 组件交互，不是浏览器布局验收。未新增浏览器截图、真实键盘全流程、200% 缩放或屏幕阅读器实测；历史截图不计本轮通过。

## 数据与安全

只使用已核对端口的隔离 PostgreSQL。迁移试验只操作 fixture 新建随机克隆库；结束清理由 fixture 删除自己创建的随机库，不删除原容器/卷。未读写 `.env`，未连接或清空真实库。

R1 的当前凭据筛查、R2 的预先提交意图、R4 的维度汇总、R7 的版本契约、R9 的清空语义、R10 的标签路径、R11 的实际解绑均可在源码和现有回归中定位；这不替代新增边界验证。本轮没有发现新的直接密钥回显证据。V2-4 明确证明意图不会随结果回滚而消失，但结果层仍会丢失已知用量。

## 模型验证

全部模型响应为明确的 MockTransport 模拟；V2-3 是真实回环 HTTP 协议模拟。真实供应商请求 **0 次**，未使用真实模型密钥。迁移与配置交错验证使用真实 PostgreSQL，不使用 SQLite。

## 未通过与未执行

未通过：以上 5 项独立断言。未执行：本轮完整 Compose 浏览器端到端、真实缩放、真实供应商、OS 进程强杀、真实 TLS、生产迁移/备份恢复。此前两项外部验证与真实进程强杀的未验证状态继续保留。

## 验收判断

第二版有实际修复，但原有测试全绿不足以确认全部关闭。V2-1/V2-2 关联 R3，V2-3 关联 R6，V2-4 关联 R5，V2-5 关联 R8；修复验收中关于迟到结果、迁移失效、总时限和立即回填的结论需要在修复后补证。**本次代码复审不通过，当前剩余问题不只外部验证。** 未授权或实施阶段 03。
