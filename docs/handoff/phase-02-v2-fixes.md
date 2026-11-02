# 阶段 02 第二版复审修复交接

日期：2026-11-02。验收：[phase-02-v2-fixes.md](../acceptance/phase-02-v2-fixes.md)。证据：[索引](../acceptance/evidence/phase-02-v2-fixes/README.md)。

## 本阶段范围

修复 V2-1～V2-5：迟到结果、旧数据指纹、总截止时间、Unicode 元数据、部分保存刷新失败。浏览器验收发现手机公共抽屉被裁切，同轮修复。阶段 03 未开始；真实缩放/供应商/进程强杀仍未验证。

## 工程入口

- `backend/app/services/model_centre_service.py:_finish_row`：provider→model 加锁重读、配置匹配后更新当前能力。
- `backend/app/domain/model_evidence.py`：所有读取出口统一能力有效性判断。
- `backend/app/providers/adapter.py`：同步 `probe`、异步 `probe_async`、总时限取消、UTF-8 校验。
- `backend/alembic/versions/8c24a71d9e03_repair_provider_evidence.py`：修复已有数据，必须在部署时执行。
- `frontend/src/views/OrganizationView.vue`：提交响应立即回填，刷新失败保留草稿。
- `frontend/src/components/AppDrawer.vue`：Teleport 根元素上的移动端宽度映射。
- `backend/tests/test_v2_fixes.py`（11 项）；`frontend/src/__tests__/v2-fixes.spec.ts`（2 项）；`scripts/verify_phase02_v2_ui.cjs`（4 视口真实 SPA、模拟 API）。

检查入口：后端 `ruff check .`、`ruff format --check .`、`mypy app`、`pytest -q`；前端 `pnpm check`；根目录 `node scripts/verify_phase02_v2_ui.cjs`（需 NODE_PATH 指向已有 Playwright）。数据库测试设置 `TEST_ADMIN_DATABASE_URL`，只指向隔离实例。

## 变更

HTTP API 请求/响应字段不变，expectedVersion 仍必填。无新表或列，新增一条数据迁移，头为 `8c24a71d9e03`。旧未知归属能力作废，已知正确能力和全部探测/用量/审计保留。降级此迁移只回退版本号，保留修复数据，不恢复错误验证；往返实测。生产升级须先备份并停止写入，具体命令见验收报告。

内部传输注入改为 `httpx.AsyncBaseTransport`，MockTransport 继续可用；真实测试用 AsyncHTTPTransport。同步服务仍调用 `probe`；异步调用者应 await `probe_async`，不要在正在运行的事件循环里调用同步包装。未新增依赖、重试或演练开关。TLS 信任库在进程启动时加载，证书配置变更后重启服务。

## 关键决定

见 [ADR-0003 新追加记录](../decisions/0003-phase-02-organization-and-model-centre.md)。保留已执行迁移，追加数据修复；写入时在锁内刷新 ORM 身份映射；所有展示出口共享同一规则；超时取消实际 I/O，不把阻塞调用丢进遗留后台线程。

## 验收

后端 **170 passed**、前端 **93 passed**；ruff/format/mypy、前端完整检查通过。新增后端 11 项通过；1 秒慢头/停滞正文分别约 1.007/1.025 秒退出且服务端看到断开。四视口浏览器模拟 API 恢复流程通过，截图已人工查看。

不是完整 Compose 端到端重跑；没有真实模型、真实缩放或 OS 强杀通过结论。原复审及前轮修复证据未改。隔离 PostgreSQL 恢复停止、容器与卷保留，既有 Compose 栈未动。

## 下一阶段接入点

后续先执行新迁移，复用统一适配器和能力有效性 helper；任何新能力出口都不能直接信任裸 supports_json_output。探测意图仍预先提交，PENDING 不等于零用量。历史结果仍逐次保留。外部验证缺口关闭前，不将阶段 02 标为完整通过。本交接不授权阶段 03。
