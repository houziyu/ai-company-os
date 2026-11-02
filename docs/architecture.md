# 技术架构约束

## 已确定实施基线

FastAPI / Pydantic / SQLAlchemy 2 / Alembic；PostgreSQL；Vue 3 / TypeScript / Vite / Router / Pinia / Element Plus；Docker Compose。具体兼容版本在阶段 01 验证并锁定，禁止在计划文档猜测最新版本。
目标是单所有者、单公司、多项目的受控研发系统。由用户部署在其信任环境中，不宣称支持敌对多租户。

## 运行组成

| 单元 | 责任 | 禁止 |
|---|---|---|
| web | Vue 静态资源、同源 API/SSE 入口 | 存储模型密钥、决定业务授权 |
| api | 认证、业务命令、查询、审批、预算 | 在 HTTP 请求中运行研发任务 |
| worker | 领取执行、模型调用、上下文、阶段推进 | 直接运行不可信代码或修改终态绕过门禁 |
| executor | 接收固定协议、按策略创建任务容器、收集证据 | 接收任意 Docker 参数、向任务暴露管理凭据 |
| postgres | 业务事实、事务队列、账本、事件 | 依赖内存状态作为唯一事实 |
| migrate | 部署时单次迁移 | API/Worker 同时竞争迁移 |

代码单体、进程分开；模块分为 organization、models、projects、tasks、governance、budgets、memory、audit。API → application services → domain/repository。provider 和 executor 通过接口接入，不能把供应商响应格式渗透到所有业务模块。

阶段 02 的落地位置：`app/models/organization.py`（公司/组织/模型表）、`app/services/organization_service.py`、`app/services/model_centre_service.py`、`app/providers/adapter.py`（唯一与供应商通信的模块）、`app/api/v1/{organization,model_centre,audit}.py`。业务代码不得直接使用 `httpx` 调供应商；新增供应商必须扩展适配器，而不是在各服务里复制请求逻辑。

公司唯一性由 `companies.id = 1` + 检查约束保证，迁移不预置公司行；模板内容在 `company_template_positions`（迁移种子，只读），实例化复制到 `positions`/`position_versions`/`agents`，模板后续变更不影响既有公司。模型绑定、岗位职责版本与员工职责履历都是追加式，删除路径不存在（外键 `RESTRICT`，无级联）。

供应商凭据只以**环境变量名**（`providers.secret_ref`）进入数据库，schema 中没有任何可存密钥值的列；`secret_configured` 在读取时由进程环境计算。模型能力字段（如 `supports_json_output`）为“未验证/已验证/未通过”三态，只有探测解析成功才置为已验证。

## 权限与运行

角色定义、Agent 身份、运行模型分离。身份 Prompt 描述职责，不赋予资源权限。每次操作都检查主体、动作、资源、项目、任务、预算、有效期、租约和公司策略。委托默认不能再委托。
单次执行保存岗位版本、模型配置版本和政策版本。高风险政策收紧时重检待执行任务，历史记录保留原版本。
工作流状态由数据库迁移规则推进；进程崩溃后不得仅凭“上次正在执行”重新运行未知副作用。

## 事务与预算

业务实体显式建表。批准、预算预留、任务创建、outbox 事件在同一事务内提交。锁顺序统一（公司→项目→任务），避免并发死锁；余额非负约束与幂等唯一约束落到数据库。
事件按至少一次处理设计，消费者保存游标并去重，不声称跨外部 API 恰好一次。
Token 账本区分 reservation、reported usage、conservative estimate、settlement；发生未知用量不能记为 0。金额必须有币种、价格版本，未配置价格展示“未估算”。
Web 页面显示三种独立事实：业务状态、执行阶段、员工状态；不能用“在线绿点”替代三者。

## 上下文与模型

provider 请求超时、输出上限、结构校验、有限重试；错误映射稳定，不输出远程原始错误。阶段 02 的适配器对探测**不做**自动重试（一次探测最多一次调用，数据库约束保证），超时在 1–120 秒夹取，错误映射为稳定 `ProbeErrorCode`；供应商正文、响应头与被回显的密钥一律丢弃。后续阶段若要重试，必须先把策略写入配置并保证总量可核算，不能默认重试。
Context Builder 只加载授权项目资料和经过过滤的记忆；记录来源及大小。外部内容不覆盖平台安全规则。
只保存任务必要的可审计摘要，不要求或依赖模型私有推理过程。
供应商配置保留逻辑模型 ID 和实际返回信息（如有），不可凭别名猜测已调用具体版本。

## 执行隔离

普通 Agent/API/Worker 无宿主 Docker socket。执行器若为容器管理持有 socket，则其自身是高信任组件，须内部认证、白名单镜像和挂载、限流；只暴露固定任务协议。socket 代理不等于零宿主风险。
每执行独立工作区；任务容器非 root、无网络、只读根、受限 tmpfs、capabilities 清空、CPU/内存/PID/输出/时间上限。代码和验收文件分离、只读验证。API 密钥不挂载到任务容器。
隔离器失败时 BLOCKED；没有宿主执行兜底。容器共享内核的边界必须在部署文档说明。

## Compose

默认只有 web 入口对外；数据库和执行器通过内部网络访问。首次配置生成或要求唯一部署秘密，不内置公开口令。
提供 .env.example（非秘密示例）与 secret 文件说明；数据卷分离数据库、产物。不能用 docker compose down -v 作为普通升级或修复步骤。
初始化与迁移需要健康检查、幂等、失败即阻止就绪。备份涉及数据库与产物一致性，记录备份版本、UTC 时间、应用版本和校验和。
停止/重启不保证运行中外部调用立即取消，恢复规则需明确；镜像更新与数据库迁移可回退性分别说明。

## 开发阶段

阶段 01 可先启动无业务 Worker；阶段 04 才启用执行器。开发阶段尚未实现的服务应有清晰状态，不用假健康掩盖缺失。

