# 源码深度审查与改善建议

日期：2026-10-16。审查基线：`b63970b4efb84f79e2d120d1c87d375eb0a12e41`。
目标：稳定性、可维护性与学习价值。本文保留修复前的审计快照，**审计轮次仅分析，没有修复**。
后续已按用户要求实施R1～R5，当前结果与未验范围见[审计修复与验证](20-audit-fixes-validation.md)。
下面的失败结果与结构重构建议作为历史证据保留，不将其改写为当前验收结果。

## 1. 结论与优先级

现有架构适合当前规模：FastAPI 接任务、数据库管理执行权、Worker 跑 LangGraph、受限 Agent 获取资料、确定性工具计算、程序与模型分别校验。无需推倒重写，也没有证据支持现在拆微服务或换成 Java。

值得先做一轮有针对性的修复，再进行小范围重构。本轮复现了五个现有测试未覆盖的反例：

| 编号 | 优先级 | 问题 | 已确认的范围 |
| --- | --- | --- | --- |
| R1 | P1 | 恢复期间指标数据变动，旧新证据混用，确定性报告重复 ID 并最终失败 | 临时 SQLite、真实业务工具和 Worker，全链复现 |
| R5 | P1，公网/容器试用前 | 默认代理信任配置使多个用户共用代理 IP 的登录限流桶 | 已安装 Uvicorn middleware + 真实登录函数隔离复现；未在 Docker 实测 |
| R2 | P2 | 库存 SQL 风险排序和输出分类使用不同精度 | 临时 SQLite 业务工具复现；SQL 条件与 Python 分类对照 |
| R3 | P2，启用 RAGFlow 前 | 选择 RAGFlow 后本地私有附件被检索分支跳过 | 本地附件真实入库，RAGFlow 协议替身返回空结果 |
| R4 | P2 | 同文档片段配额用 locator 字符串推导，PDF 等定位不生效 | 真实 Repository 与 evidence_package，五页同文档全部入包 |

P1 表示应优先处理的明确失效或部署阻断；R5 的优先级以多人反向代理部署为前提。P2 是明确缺陷，但有特定输入或可选功能触发条件。未发现足以支持 P0 或整体重写的证据。

## 2. 本轮验证与边界

### 2.1 现有检查

| 检查 | 本轮结果 | 说明 |
| --- | --- | --- |
| Ruff 检查与格式 | PASS | 74 个 Python 文件格式检查通过 |
| 首轮 pytest | 173 PASS，11 SKIP，3 因环境中断 | 三项都是沙箱禁止监听 127.0.0.1，并非业务断言失败 |
| 三项本地 HTTP 补跑 | 3/3 PASS | 在允许本地监听的隔离环境执行，无真实模型/外部搜索 |
| PG 相关三文件专项 | 18/18 PASS | 临时 PostgreSQL 16.2；其中 11 项补齐首轮跳过项，7 项与首轮有重叠 |
| 新构造反例 | 五项均复现 | 这些是已确认的缺陷证据，不能因现有测试通过而忽略 |

现有 187 个测试节点在上述分批运行中覆盖通过；**不是同一条命令输出 187 passed**，也不是生产验收。首轮环境阻塞和后续结果分别保留在本文。

PG 专项命令：

```bash
uv run --offline --with pgserver==0.1.4 python scripts/test_embedded_postgres.py \
  tests/test_postgres.py tests/test_postgres_api.py tests/test_postgres_backup.py
```

本轮没有重跑浏览器 E2E、32 条离线评估或真实模型用例，也没有验证实际 Docker、HTTPS、公网代理链、生产恢复、长时间压力及 Tavily/RAGFlow 外部服务。R5 是配置与 middleware 组合的隔离复现，不能称为真实容器攻击演练。

本机已安装版本：Deep Agents 0.7.25、LangGraph 1.2.15、LangChain 1.4.5、FastAPI 0.136.1、SQLAlchemy 2.0.49、Uvicorn 0.46.0、Pydantic 2.13.4。这里没有评估升级到最新版本的收益。

### 2.2 反例使用的隔离条件

临时目录、临时 SQLite、`Settings(_env_file=None, app_env="test", agent_mode="demo", knowledge_provider="local", ...)`，通过 `Base.metadata.create_all`、`bootstrap_user` 与 `seed_commerce` 建测试数据。需要外部检索的反例替换 `ToolGateway._post`，不发真实 HTTP。所有数据均为教学样例或合成输入。

## 3. R1：恢复期间数据变化会破坏报告生成

### 触发与证据

1. 建立 sources=[sales] 的 demo 任务，领取后执行一次 `ToolGateway.metrics("sales")`，销售证据已经提交。
2. 在临时库中把九月某条 `DailySale.revenue_cents` 增加 100，模拟运行期间业务数据更新。
3. 将旧租约设为到期，由真正的 `Worker.run_once` 接管。没有预先写 collect 完成检查点，模拟证据提交后尚未完成节点的恢复状态；本次没有新增 SIGKILL 演练。
4. collect 重读销售，结果不同，按内容去重不会把第二版与第一版合并。

实际结果：

```json
{
  "sales_sources": 2,
  "net_revenue_values": ["83766.00", "83767.00"],
  "duplicate_claim_ids": ["sales-net", "sales-refund", "sales-change", "sales-units"],
  "task_status": "failed",
  "error_code": "GraphRecursionError"
}
```

### 根因与源码

- [Repository.add_evidence](../app/storage/repository.py)：metrics 的内容键包含完整 payload。数据变化后，两版都合法留在账本。
- [deterministic_report](../app/research/evidence.py)：遍历所有销售来源，每份都生成固定的 sales-net 等 Claim ID。
- [graph.draft/validate](../app/research/graph.py)：使用所有账本证据；模型失败时的确定性回退也使用同一路径，校验失败后还会回到 draft。
- [现有恢复测试](../tests/test_recovery.py)：覆盖提交窗口，但恢复前业务样例没有变化，无法发现该反例。

确认的是 demo/确定性回退路径失败；不宣称所有 live 输出必定失败。live 模型可能恰好只选一版，但不能依赖模型解决程序缺少权威版本的问题。

### 改善建议

先定义同一任务中每个“工具 + 分析周期”的权威结果：建议首次成功读取后固定，恢复时复用。确实要刷新时，应明确创建新的采集版本并替换当前选中版本，历史账本仍可保留。

报告与上下文应使用明确选定的 evidence_id 集合，不能把所有历史版本都当作当前依据。不要仅给重复 Claim ID 加后缀；那会让两版相互矛盾的数值同时通过 ID 检查。

另加兜底：确定性回退本身不通过程序校验时，以固定错误码终止并保留证据，不再无效地沿 draft/validate 循环到图递归上限。

### 验收要求

增加“证据提交 → 更新本期/前期数据 → 接管恢复”测试，至少覆盖销售和库存；断言采用的版本策略、引用集、结论 ID 唯一性和终态。保留旧版本时应能追溯其未被采用的原因。现有 SIGKILL、租约和预算测试仍须通过。

## 4. R5：反向代理部署下登录限流可能误伤所有用户

### 触发与证据

[登录接口](../app/api/server.py) 把 `request.client.host` 传给 [login](../app/core/auth.py)，后者为账号和客户端地址分别建立 10 分钟限流桶。

[Nginx](../docker/nginx.conf) 转发 X-Forwarded-For，但 [后端启动命令](../docker/backend.Dockerfile) 未配置可信代理。本机 Uvicorn 0.46.0 的默认可信地址是 127.0.0.1；Docker 网桥上的 frontend 通常不在其中。完整生产链还多一层 Caddy。

隔离复现把两个不同的 X-Forwarded-For 地址经真实 ProxyHeadersMiddleware 传入，连接端均设为模拟容器代理 172.20.0.6：

```json
{
  "forwarded_end_users": ["203.0.113.10", "203.0.113.20"],
  "application_seen_clients": ["172.20.0.6", "172.20.0.6"],
  "ten_invalid_logins_with_different_accounts": 401,
  "another_users_correct_login_afterwards": 429
}
```

也就是一个访问者在共用代理桶耗尽额度，另一用户的正确登录也被拦截。该结果由真实限流函数产生；没有向用户正在运行的服务发请求。

### 改善建议与验收

明确 Caddy → Nginx → Uvicorn 的可信代理链，限制 API 直接访问，再配置受信代理及转发头处理。只信任最后一跳但没有正确处理更前面的代理，也可能仍将多个用户聚合为同一客户端。

不能直接读取任意客户端提交的 X-Forwarded-For，也不建议为了省事无条件信任所有来源。[Uvicorn 官方部署说明](https://www.uvicorn.org/deployment/)描述了代理头及 forwarded-allow-ips 的信任边界；本问题同时核对了已安装 middleware 的实际行为。

验收应包括：两个不同客户端的 IP 桶相互独立；同一账号跨 IP 的账号限流仍有效；未受信直连者伪造转发头不会改变身份；经实际 Compose 及 HTTPS 代理链核对服务端看到的来源。后两项本轮未做。

## 5. R2：库存排序与分类的精度不一致

源码：[inventory_risks](../app/commerce/service.py)。SQL 用未按展示精度舍入的 coverage 比较并排序；Python 先转两位小数字符串，再用这个值判断 stockout/overstock。

反例：一天销量 1000、库存 999、配置提前期 1 天。真实比值为 0.999 天，SQL 的 `coverage < lead_days` 为真，但展示 1.00 天、Python 返回 risk=normal。

影响不仅是显示四舍五入：风险排序和分类不一致，临界商品可能进入高风险队列却不产生相应建议。积压阈值附近也有同类问题。现有边界测试用的覆盖天数离阈值至少 0.1 天，没有覆盖被两位小数吞掉的差异。

建议明确唯一判定精度，用同一业务谓词驱动 SQL 排序、分类和报告口径；展示格式最后处理。如果选择按未舍入值判定，显示或解释应让临界差异可理解。

验收覆盖 0.999/1.000/1.001、略高于 60 天但显示为 60.00 等边界，并在 SQLite/PG 上核对排序和输出。不要只调整某个分支的常量。

## 6. R3：RAGFlow 分支跳过本地附件

源码：[upload](../app/api/server.py)、[add_document/search_documents](../app/commerce/service.py)、[ToolGateway.documents](../app/research/tools.py)。

上传接口把提取片段存入本地 documents/document_chunks。Gateway 在 local 与 ragflow 之间二选一；ragflow 分支不会再检索本地附件，也没有上传附件到 RAGFlow 的同步步骤。

反例中先存入当前用户当前会话的独特附件规则。本地检索能找到该附件；改用 ragflow，并让协议替身返回合法空结果，最终 evidence=0、status=empty。返回限制还声称“本地检索为词法基线”，与实际走的 provider 不一致。

建议分离“会话附件检索”和“组织知识库 provider”：附件始终按 owner/conversation 查本地，组织资料再按配置选 local/RAGFlow，最后统一去重、来源标记与预算。不能为组合结果而放松用户/会话隔离，也不要无区分混入全部本地样例规则。

验收包括：远端空结果仍能取得本会话附件；远端失败时明确披露远端缺口；其他用户、其他会话附件不可见；远端数据集白名单继续有效；最终报告引用能回到实际来源。

## 7. R4：同文档片段配额依赖不一致的定位字符串

源码：[evidence_package](../app/research/context.py) 使用 `split("/chars=").split("/lines=")` 派生文档键；[add_document](../app/commerce/service.py) 实际生成的是 `page=...;chars=...`、`paragraphs-and-tables;chars=...` 或 sheet 定位等。

Markdown/TXT 的 lines 前缀可能恰好归到同一键，PDF、DOCX、XLSX 等不能据此保证统一配额。本轮构造一份 PDF 的五页 locator，通过真实 Repository 入库后调用 evidence_package，五页全部选中，超过设计中的每文档两条。

单份长文档可能挤占来源数预算，影响其他来源和反证的可见性。该问题不是“每个任务必然漏证据”，但当前配额不能提供预期的来源平衡保证。

建议为证据增加结构化 source_document_id/chunk_id/provider 等身份信息，locator 仅作可读定位；按稳定文档 ID 计配额，并显式记录由于配额排除的原因。RAGFlow 还需区分 dataset/document/chunk 层次，不用字符串拼接推断全部身份。

验收覆盖 MD、PDF 多页、DOCX 多块、XLSX 多工作表、RAGFlow；同文档不超限，不同文档不误合并，重要不同来源能进入预算，完整账本和窗口哈希含义不变。旧证据保持可读，不把缺身份字段悄悄判为已满足新规则。

## 8. 值得做的结构重构

以下是可维护性建议，不额外算作已复现的业务缺陷。优先保证行为与验证边界，再拆文件。

| 当前集中点 | 具体负担 | 建议边界 |
| --- | --- | --- |
| graph.py，489 行 | 图装配、业务执行、提示、修正、语义状态和导出都在闭包中 | 保留六个节点与条件边；先抽报告生成/校验和产物交付服务，再抽资料采集 |
| repository.py，548 行 | 任务准入、执行租约、用量、证据、产物共用一个仓储 | 先明确事务与租约保护接口，最后才按 Task/Execution/Evidence 职责拆分 |
| App.tsx，772 行 | 表单、认证、提交幂等、轮询、详情与附件状态耦合 | useAuth/useTaskSubmission/useTaskFeed + TaskComposer/TaskDetail 等小范围抽取 |
| evidence/package/state 的大量 dict | 字段约定跨模块传播，缺键和状态组合错误较晚暴露 | 对 EvidenceRecord、EvidencePackage、ResearchValidation、ReportValidation 建明确类型与边界校验 |

### 8.1 工作流应保留的核心不变量

- `task_id` 和 `task_id:research` 的线程边界、sync durability 不变。
- checkpoint 与 pending writes 都受租约保护，不把校验与写入拆开解锁。
- 仍由服务端绑定 RunContext，不让模型传权限字段。
- 所有模型请求继续走 meter，包括 SDK 内部请求；不能在抽服务后漏计费或重复计费。
- 研究失败、报告回退、语义不可用、人工驳回和任务终态不能合并成一个 success 布尔值。
- 保留一次业务修正、未知用量停止和总时限语义，不因抽象而增加隐式重试。

建议服务通过明确输入和结果交互，让 LangGraph 节点继续做轻薄编排。不需要给每个函数套一层接口，也不建议一次性把同步仓储全改成 AsyncSession。SQLAlchemy 的 Session/事务生命周期需要显式管理，不能因拆仓储把原来同一事务变成多次提交；参阅[官方 Session 说明](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)。

### 8.2 前端还有一个明确的效率改善点

App 对选中任务固定每 1.2 秒重新请求详情、增量事件和**全部证据**，任务终态后也不停止；另有每 2 秒任务列表轮询。长期停留的一个页面约每秒产生 3 个请求，证据大时重复传输明显。

建议：终态后完成一次事件追平便停止重复详情/证据请求；证据按新增版本或 ID 更新；页面隐藏时退避；waiting_review 和 running 保留必要刷新。任务列表也需要独立的分页与刷新策略。先改善读取策略，不必立即切 WebSocket 或重写 SSE 前端。

验收用浏览器请求计数比较“运行中/待复核/已结束/页面隐藏”，并保留刷新恢复、事件去重、切任务防旧响应覆盖和跨账号清理测试。本轮只做源码确认，没有浏览器请求计数实测。

### 8.3 Worker 异常监督可作为下一步可靠性专项

`heartbeats` 只处理租约/取消异常，数据库连接等错误可使心跳协程结束；`run` 会丢弃 done jobs，没有统一消费未处理异常、记录安全诊断与退避策略。当前后续租约检查和接管仍提供保护，不能据此断言一定永久卡死。

建议下一轮用短暂数据库不可用反例明确期望：心跳失败后的执行去留、重连/退避、终态诊断和领取循环存活，再决定如何监督协程。本轮未做故障注入，因此只列结构改善，不与 R1～R5 的已复现缺陷混算。

## 9. 建议实施顺序

1. **先补五项回归，修 R1 与 R2。** 核心是恢复结果版本选择、确定性回退的失败收敛、风险精度一致。不要先大规模移动代码。
2. **统一来源身份并修 R4。** 使用结构化文档/片段身份，保留账本、引用、选择清单与历史可读性。
3. **按使用条件修 R3 与 R5。** RAGFlow 启用前补附件组合；公网/多人代理试用前补可信链与限流隔离。两项可分别推进，但都需对应环境验收。
4. **再拆报告服务、前端状态与轮询。** 用 demo 的报告/引用/状态契约、SDK 替身和恢复测试守住行为。最后评估是否确需拆 Repository。

每一阶段都应独立提交，保留失败反例与测试。凡修改 app/evaluations，重算实现指纹；既有三个真实模型单例继续作为原版本历史证据，不能自动给新实现背书。是否重跑真实模型应按明确范围、费用和授权另行安排。

暂不建议：Java 重写、微服务拆分、引入 Redis/MQ 替代当前任务表、自动长期记忆、远程多 Agent、MCP/A2A 或无依据升级框架。这些都不是解决本轮五个问题的必要条件。

对学习而言，优先读懂并修复 R1/R2 比“多加一个 Agent”更有价值：它们能训练状态恢复、数据版本、业务口径和可复现验证。上一轮“开发完成”指约定范围已交付，不代表此后审查不会发现边界缺陷。
