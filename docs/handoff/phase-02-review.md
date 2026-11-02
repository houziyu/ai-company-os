# 阶段 02 独立质检交接

日期：2026-10-30（Asia/Shanghai）。

## 本阶段范围

完成当前阶段 02 代码与业务逻辑审查、原有检查回归、独立失败探针及报告；**没有修改业务实现或进行缺陷修复**。未进入阶段 03。

发现 11 个独立问题：3 个 P1、7 个 P2、1 个 P3。详见[独立质检报告](../acceptance/phase-02-review.md)，该报告补充原验收，不覆盖或改写历史记录。

## 工程入口

- 报告：[phase-02-review.md](../acceptance/phase-02-review.md)。
- 证据与复现：[证据索引](../acceptance/evidence/phase-02-review/README.md)。
- 源码快照：[SHA-256](../acceptance/evidence/phase-02-review/source-sha256.json)。目录没有 Git 元数据，不能据此推断提交历史。
- 后端重点：`app/providers/adapter.py`、`app/services/model_centre_service.py`、`app/repositories/organization_repository.py`。
- 前端重点：`src/views/OrganizationView.vue`、`src/views/ModelsView.vue`。
- 检查命令按证据索引执行；只对隔离测试 PostgreSQL 设置 `TEST_ADMIN_DATABASE_URL`。不得指向真实业务库。

## 变更

新增质检文档、探针、输出和摘要，并更新 `docs/START-HERE.md` 指向最新质检结论。无应用 API、schema、迁移、依赖、token 变更。前端探针执行时临时放在测试目录，已移出并保存至证据目录；前端构建重新生成了既有 `dist` 产物。

测试使用独立容器 `acos-phase02-review-20261030` 和端口 `55434`；结束后停止容器，保留容器及其数据，不执行卷删除。既有栈未启动或重建。

## 关键决定

沿用现有架构和 [ADR-0003](../decisions/0003-phase-02-organization-and-model-centre.md)，本轮无新产品或架构决策。探针按正确业务行为断言，失败用于保存缺陷证据，不放宽生产校验。

## 验收

后端静态检查通过、原有 141 项测试通过；前端 lint/typecheck/83 项测试/构建通过。新增后端 11 个断言、前端 3 个断言失败，复现 11 个独立缺陷。R8 在前后端重复验证，R5 有三个异常输入。

没有新增浏览器截图/真实缩放/真实供应商/生产迁移或备份恢复通过结论。完整质检结论为**失败**。

## 下一阶段接入点

1. 优先关闭 R1 凭据回显、R2 调用意图未提交、R5 外部元数据使调用记录回滚。
2. 修复配置变更失效与版本冲突、未知用量、总超时、员工编辑部分成功恢复。
3. 关闭前端键盘路径与不生效的解绑选项，修正备注清空契约。
4. 将独立探针转为合适的正式回归测试，并保留修复前证据。R8 的断言应随原子保存或分步保存的新契约调整，不能仅删除测试。
5. 修复后再跑相应回归与需要的浏览器门禁；不得仅凭原有 141/83 项通过宣称本轮缺陷已解决。
