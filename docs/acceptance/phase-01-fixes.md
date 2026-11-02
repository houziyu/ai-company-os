# 阶段 01 质检修复验收

日期：2026-10-30（Asia/Shanghai）  
对应阶段：01，独立质检 R1–R6 与静态补充项；阶段 02 未开始。  
代码版本/分支：当前目录没有 `.git`，没有提交哈希；直接增量修改当前授权工作区。  
本报告结论：**修复与已执行回归通过；完整 UI 门禁部分完成（实际浏览器 200% 缩放未验证）。不据此宣称阶段 01 所有门禁全部通过。**

环境：Windows、Python 3.13.12、PostgreSQL 17、已锁定的 FastAPI/Vue/Element Plus 依赖，未升级依赖。浏览器为独立 Chrome 156.0.8078.48，默认测试使用无头模式和独立临时浏览器上下文。

## 需求与证据

先阅读所有根目录约束及本阶段文档，再运行原始探针：后端 4 failed，前端 2 failed，全部复现原报告；没有对 R1–R6 结论提出异议。[修复前后端输出](evidence/phase-01-fixes/backend-before.txt)、[修复前前端输出](evidence/phase-01-fixes/frontend-before.txt)。原始 `phase-01-review/` 的探针与结果没有修改。

| ID | 实际修复 | 正式回归与实测结果 | 证据 |
|---|---|---|---|
| R1 | `DbSession` 使用 FastAPI function 作用域，commit 在发送状态、响应体和 Cookie 之前完成 | 初始化/登录/注销 commit 故障均为 503 `DATABASE_NOT_READY`，不发送 Set-Cookie，事务内用户/会话/审计均回滚；注销失败后旧会话仍可验证，再次注销成功后失效 | `backend/tests/test_auth_regressions.py`，后端回归日志 |
| R2 | INFO/DEBUG 均将 SQLAlchemy 日志限制为 WARNING；引擎隐藏参数；事件文本和 extra 字段白名单；异常只含类型与栈位置 | 两日志等级下实际密码哈希、会话/CSRF 哈希和原令牌均未进入 JSON 输出；任意 extra、插值消息和异常值被排除；新 API 容器日志布尔扫描无对应泄漏 | `backend/tests/test_logging.py`，[运行日志检查](evidence/phase-01-fixes/runtime-log-check.json) |
| R3 | 单所有者阶段复用 PostgreSQL `platform_state` 行锁，包住读取计数、密码验证、失败记录/清零及提交，包括未知用户名 | 四种组合（单 app/独立 app + 已知/未知用户）：阈值 3，已有 2 次失败，并发 4 次结果恰为 1×401 + 3×429；失败记录恰为 3，锁定审计为 3 | `backend/tests/test_auth_regressions.py` |
| R4 | 认证读取会话时加行锁；过期撤销及审计在抛出 401 前提交 | 4 个并发及重复请求全部 401，持久化 `EXPIRED`，仅 1 条 `SESSION_EXPIRED`；提交故障返回 503，状态和审计一起回滚 | 同上 |
| R5 | 受保护 API 的 401 集中通知会话 store；失效页面提供登录入口；两个注销入口共用错误/重试逻辑 | 原路由断言通过；网络错误/503 保留可见“注销结果尚未确认，服务器会话可能仍然有效”，可重试；注销 401 按已无有效会话处理。实际服务撤销会话后，SPA 收到 401 可直接进登录页 | `auth-regressions.spec.ts`、`logout-regressions.spec.ts`、`api-client.spec.ts`，[浏览器结果](evidence/phase-01-fixes/ui-results.json) |
| R6 | 确认 `SETUP_ALREADY_COMPLETED` 时更新 setup 缓存，再跳转登录并显示提示 | 真实页面/守卫/传输测试通过；浏览器中用陈旧 setup-status 缓存向真实服务提交，收到真实 409 后进入 `/login?setup=completed` | 同上，[截图](evidence/phase-01-fixes/setup-conflict-login-1440x900.png) |
| 平板导航 | 九个导航含独立名称和当前页语义；折叠时启用 hover/focus 提示 | 1024px 名称完整，聚焦显示“设置”提示，Enter 进入设置；单元测试检查全部九项 | [聚焦截图](evidence/phase-01-fixes/tablet-navigation-focus-tooltip-1024x768.png) |
| 验收脚本 | 按 Compose 项目定位运行中的 postgres，实际检查 HostConfig.PortBindings；无法检查则失败 | 正式测试覆盖未发布/发布端口、无容器、损坏输出及 Docker 不可用；真实隔离栈端口检查通过 | `backend/tests/test_acceptance_script.py`，[初次栈验收](evidence/phase-01-fixes/compose-acceptance.txt) |
| Worker 语义 | 保留 API/Worker 分别健康，概览显示“API 可服务”，说明 Worker 独立检查 | 后端原有门禁/过期心跳测试通过；新增前端测试证明 Worker 缺席仍显示 API 可服务并展示缺失原因，不显示“全部就绪” | `test_health.py`、`auth-regressions.spec.ts` |
| 入口文档 | 更新 START-HERE、README、原验收/交接的当前状态说明、ADR 健康语义更正 | 当前事实、历史证据与未验证项可区分；未改写原始质检证据 | 本报告及修复交接 |

复现测试转为正式回归时保留了原行为断言并增强持久化验证。R3 屏障从“读计数后”移到“加锁前”，因为加锁后只有一个请求能到达读计数点，原屏障会制造测试自身死锁；没有修改真实计数、密码验证或阈值。R5/R6 的 API 替身改在 fetch 层，使真实客户端的集中 401 处理也纳入验证。默认测试没有跳过、删除旧断言或放宽生产校验。

实际执行（目录与环境见修复交接）：

| 命令 | 结果 |
|---|---|
| backend：`python -m ruff check . ../scripts/verify_phase01.py` | 通过 |
| backend：`python -m ruff format --check . ../scripts/verify_phase01.py` | 42 文件符合格式 |
| backend：`python -m mypy app` | 31 源文件通过 |
| backend：独立 PG 下 `python -m pytest -q --tb=short --show-capture=no` | **65 passed，4 个原有依赖弃用警告，150.19 秒** |
| 最后日志白名单元信息/验收脚本输入验证调整后：`pytest tests/test_logging.py tests/test_acceptance_script.py -q` | **9 passed**；完整 65 项之后仅这些相关代码变化，未再次重复无关测试 |
| frontend：`pnpm check` | tokens/lint/typecheck 通过，**55 passed**，Vite build 通过 |
| 隔离 Compose `up -d --build` | 新卷、新镜像标签；四服务 healthy，migrate 退出 0 |
| `verify_phase01.py --base-url http://127.0.0.1:18081 --compose-project acos-phase01-fixes` | 新栈 **21/21**；最终已有账号栈 **20/20**（不重新执行首次创建专项） |
| `node scripts/verify_phase01_ui.cjs` | **21 项浏览器检查通过**，无未捕获页面异常；含截图、键盘、尺寸、对比度与失败恢复 |

完整输出在 [修复证据目录](evidence/phase-01-fixes/README.md)。

## UI 检查

- 保存 1440×900、1024×768、390×844 三视口及 360×800 的真实页面截图，覆盖默认/空、加载、错误、禁用和提交中。截图 fullPage 允许高于视口，测试启动的视口尺寸不变。
- 实际执行 Tab → 登录名 → Tab → 密码 → Enter，验证错误密码反馈、保留登录名、提交中禁重复提交及登录成功；验证注销错误重试、会话失效与初始化冲突后的登录入口。
- 1024px 九个图标导航都有可访问名称，提示支持键盘焦点；手机导航抽屉 Escape 关闭后返回菜单按钮。注销弹窗 Tab 焦点不逃出，Escape 后返回触发按钮。
- 手机菜单 44×44、注销 64×44；公共按钮 32/40/48px。修正了本轮截图发现的弹窗根样式未作用于 teleported 元素问题，边距/最大高度使用既有 token；`dialogMaxHeightVh` 生成单位修正为 vh。390px 下弹窗 x=16、宽358、高451，不超 85vh。
- 检查整页宽度，无横向溢出。另检查 720×450 等效重排；**这不是实际浏览器缩放验证**。
- 实际渲染对比度：主按钮默认/hover/active 为 6.70/8.72/10.36；九种展示状态最低 4.79；输入边界 4.76、焦点 5.17，焦点轮廓 2px。范围为这些已测组合，不声称全应用 WCAG 认证。
- 使用图片查看工具逐张检查关键三视口、平板聚焦、移动注销失败弹窗、会话失效与初始化冲突截图：内容可读，无裁切与按钮遮挡；新增页级反馈与正文有 token 间距。浏览器自动化与模型视觉检查不等于真人屏幕阅读器检查。

## 数据与安全

- 集成测试仅使用 `acos-review-pg-20261029`（127.0.0.1:55433），夹具创建/清理随机 `acos_test_*` 数据库。Compose 浏览器测试仅使用新项目 `acos-phase01-fixes` 和新卷 `acos-phase01-fixes_postgres_data`。
- 原 `ai-company-os` 服务保持停止状态，未启动、重建或注入故障；没有修改/清空现有业务数据、覆盖 `.env` 或删除任何已有卷。测试栈使用 `--env-file .env.example` 和进程级测试配置。
- 无 schema 或迁移变化，不需要数据转换/回填。升级前备份及迁移验证说明见修复交接；本轮没有执行真实库升级、降级或恢复。
- 没有推送、发布、外发消息或生产部署。所有模型、预算、执行器能力仍不在阶段 01 范围。

## 模型验证

不适用。没有演练模型、模拟 Provider 或真实模型调用。

## 未通过与未执行

- **实际浏览器 200% 缩放未验证**：浏览器连接通道不可用；随后选择专用 Chrome 窗口时，Computer Use 因无法可靠识别当前浏览器 URL 而停止，未继续发出桌面输入。早先自动配置缩放的尝试实际仍为 100%，仅留作 `zoom-attempt-actual-100.png`，不算 200% 证据。已通过的只是 720×450 等效重排。
- 真人屏幕阅读器、真实 HTTPS/Secure Cookie、跨机器/真实网络断电下提交、性能压力与拒绝服务负载、依赖漏洞扫描、真实数据备份恢复均未执行。
- 登录复用单例行锁是单所有者阶段的保守串行化选择，可能降低并发登录吞吐；没有声称完成压力测试。
- 原容器的历史日志可能仍含修复前敏感哈希。本轮不删除用户日志/数据；新代码和新测试栈已止漏，不意味着历史日志已清理。
- 曾尝试把“停止隔离 Worker、更新测试心跳、读 readiness”合成一次命令，自动审批审查拒绝执行，工具只返回“blocked by policy”，未提供更具体原因；该操作未执行。Worker 退化行为以真实 PG 自动测试与前端回归证明，没有将未执行的运行栈故障注入算为通过。
- 已存在的 4 个后端弃用警告未通过降低告警级别来隐藏；本轮未升级相关依赖。

## 验收判断

R1–R6 和静态补充项均已增量修复并有正式回归；没有剩余已知的本轮阻断性代码缺陷。当前交付可供复核修复结果，但实际 200% 缩放 UI 门禁仍未完成，故整体记为**部分完成**，不自动进入阶段 02。原独立质检报告与证据保留原样。
