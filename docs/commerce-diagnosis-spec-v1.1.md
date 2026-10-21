# Commerce 电商经营诊断 Agent 开发文档

版本：1.1｜复审日期：2026-10-17｜性质：待实施的大规模二次开发方案

本文适用于 commerce-analysis-agent。用户已授权根据当前源码与求职目标选择改造方向，本文选择：在现有电商经营分析场景内，建设可受控执行多步诊断、按需下钻、核验假设并输出可追溯报告的 Agent。保留固定复盘作为基线。业务操作只读，不自动改价、采购、发券或发送消息。

本文是开发契约，不是新能力已实现、模型效果已达到目标或项目已经生产验收的证明。

## 1. 目标与交付

用户应能提出：“本期净销售额为什么下降，主要涉及哪些商品，退款变化能解释多少，哪些还需要补数据？”系统先确认数据/周期，计算基准，再由 Agent 在授权工具范围内选择下钻步骤，逐步检查候选解释，最终输出事实、证据支持的观察、未证实假设、数据缺口和核对建议。

不能把用户的“为什么”自动回答成已证明因果。没有流量、渠道、成本和订单关联数据时，不得声称识别转化率下降、利润下降、广告ROI或某批订单最终退款率。

必须交付的增量：

1. 经营诊断新入口，任务绑定不可变数据组合及算法/图/Prompt版本。
2. 结构化诊断计划、模型按需选择的业务工具、执行后的证据检查及受限重新规划。
3. 至少SKU、品类和日维度的真实下钻；金额、变化与退款分析使用确定性计算。
4. 假设与证据的支持/反驳/不足状态，能够停止无意义探索或请求澄清。
5. 新循环在重启、接管、复核、取消和预算不足时仍能恢复、终止和解释。
6. 诊断过程、事实证据、报告和反馈的产品界面，独立离线与真实模型评测。
7. 与固定流程公平对比，保存失败，不预设Agent必然比Workflow更好。

这不是再包一层聊天页面。核心新增内容是诊断决策循环、受控工具与可验证的业务结论，而不是重复添加已有认证、租约或PDF功能。

### 1.1 可行性结论与关键取舍

方案可行，现有项目已经具备固定复盘、不可变销售数据、指标计算、任务租约、检查点和导出，可把主要工作投入诊断循环与新业务契约。实际难点是让模型选择有价值的下一步，同时保证数据口径、恢复后的调用身份和证据支持关系一致。

保留现有LangGraph/SQLAlchemy/PostgreSQL和前端体系；主链采用单Agent，不新增另一套任务队列、通用SQL执行器或企业级知识库。库存与规则文档仍完成，但在销售诊断稳定后作为明确阶段接入。G0—G7均是最终方案，不安排工期或每周投入。

“动态”应有可见差别：同样是销售下降，有的任务需要核查退款金额，有的需要核查SKU贡献、日期结构或缺数；工具顺序和停止点由问题与中间证据决定。若所有任务最终总是遍历固定工具表，新入口仍只是更贵的Workflow，应先修正任务设计再扩框架。

本次复审重点：修复旧证据快照与筛选查询的冲突；把澄清限定在固定数据包内；补齐模型调用持久账本与预算原子性；将数值和可验证观察由程序渲染；把阶段表展开为能直接执行的工作清单。

## 2. 当前基线与证据范围

### 2.1 版本

- 仓库：/Users/zzds/Workspace/gitStore/commerce-analysis-agent
- HEAD：47bfc1ba1546ab24a9059d014687961e71f438bc
- 本轮查看时工作区干净，未同步远端。
- 当前声明依赖：Python >=3.12,<3.14；Deep Agents 0.7.25、LangChain 1.4.5、LangGraph 1.2.15，FastAPI、SQLAlchemy/Alembic、PostgreSQL/SQLite；前端React/TypeScript/Vite。
- 本轮仅静态读源码和现有文档。历史测试条数、模型单例、耗时均没有在本轮重跑，不作为新方案验收证据。

### 2.2 已有能力与实际边界

| 当前入口（仓库相对路径） | 已有事实 | 增量要求 |
| --- | --- | --- |
| app/research/schemas.py：TaskRequest | profile只有research、commerce_review；后者强制sales+workflow+dataset | 新增独立commerce_diagnosis契约，保持旧校验 |
| app/commerce/datasets.py、review.py | 所有者隔离的不可变经营CSV版本、质量检查、确定性指标与贡献分解 | 复用；新增有筛选参数的下钻，而非重写全部计算 |
| app/research/graph.py | plan→collect→draft→validate→review→export | 诊断图含选择/执行/评估/停止循环，不能仅改Prompt |
| 同文件commerce_review分支 | 模型选择候选假设代码及建议，关键事实由程序生成 | 让模型选择下一步核查动作，但仍不允许改写数值事实 |
| app/research/tools.py：ToolGateway | 授权来源、受限查询、工具计数、证据登记、旧指标快照复用 | 增加显式参数Schema与调用账本，快照键纳入筛选和数据版本 |
| app/tasks/worker.py、checkpoints.py、storage/repository.py | 任务队列、lease token、checkpoint fencing、取消、复核、预算 | 扩展到新图/新中断类型和循环恢复；不是“从零加可靠性” |
| app/research/agents.py、meter.py | 模型客户端已关闭SDK重试；回调在调用边界计数和记录usage，接管时检查未报用量 | 保留现有保护；新增调用身份/输出账本，不重复扣预算或假定现有计量不存在 |
| app/commerce/service.py | 高级研究销售/库存工具读取旧业务表，内部文档可词法检索 | 新诊断数据组合要统一绑定版本；不能继承跨工具快照不一致 |
| app/api/server.py、frontend/src | 数据导入、任务、报告、证据、事件与人工复核 | 新增诊断计划/步骤/澄清/反馈视图 |
| evaluations/commerce_review.py、run.py、tests/ | 固定复盘与旧研究的评测/恢复/权限等测试入口 | 新诊断测试集、逐步轨迹判断和公平基线；保留原回归 |

旧高级研究有Deep Agents、单/多Agent策略，但固定复盘主入口不调用Deep Agents。不能把配置中存在multi说成每个任务都发生了多Agent协作，也不能把固定模板报告称为自主多步诊断。

## 3. 产品与能力边界

### 3.1 典型任务

| 场景 | 期望行为 |
| --- | --- |
| 净销售额下降 | 先检查可比性，按贡献找SKU/品类，再选时间结构或退款核查；输出可观察事实，不补造原因 |
| 退款占比变化 | 同时检查支付额、退款额、退款件数与分母；不从占比上升推断退款金额上升 |
| 头部商品变化 | 定位具体SKU及周期，按日下钻，不以一次波动断言长期趋势 |
| 商品新增/停报 | 按生命周期和记录覆盖区分缺数与明确零值；缺少下架元数据时提示边界 |
| 库存风险（第二阶段业务扩展） | 有版本化库存输入才计算，说明快照日期/销售窗口/提前期依据；无数据则拒绝估算 |
| 规则解释 | 在授权且固定版本的规则文档中检索并逐字关联引用，区分规则与实际原因 |
| 问题不明确 | 创建前补齐数据集/周期；运行中只澄清已绑定范围内的对象和问题意图，保留已取得证据 |
| 要求改价/下单 | 明确本项目只读，给出需要人工处理的建议，不执行业务写入 |

数据来源仍由用户选择/服务器配置决定；模型不能自行扩大到其他用户、任意网址、数据库、文件系统或外部平台。

### 3.2 保留与扩展

保留commerce_review为稳定固定流程入口与对照；保留原research高级入口，并明确其旧存储/快照边界。新诊断入口使用commerce_diagnosis，默认作为主要新增产品能力。

首版主循环用LangGraph实现一个Agent的受控决策，不要求默认多Agent。多Agent作为后续对照实验，仅在同输入/工具/预算下评估，不以框架数量充当改造规模。MCP、远程子Agent、模型训练与业务写操作不列入本版交付。

RAGFlow/Tavily适配器保留，但新诊断闭环不以外部服务可用为前提。库存和内部规则扩展用本地可复现版本先交付；外部竞品搜索沿用高级研究，公开资料不能直接证明内部经营因果。

## 4. 数据版本与业务口径

### 4.1 复用现有销售CSV

现有字段：

~~~csv
sku,name,category,active_from,day,paid_units,refunded_units,revenue_cents,refund_cents
~~~

金额按整数分，日期按记账日，SKU/品类/启用时间来自确认后的版本。保留缺行不补零、跨期退款可大于当日支付等语义。退款金额不是利润；当前没有成本、曝光、访客、订单ID和退款原订单关联，相关问题返回unsupported_metric或evidence_gap。

新工具过滤范围只允许任务授权SKU/品类和本次/对比期。计算对比期时必须展示具体日期；不允许模型悄悄选择不同周期以“找到原因”。首版默认等长前一期，用户可在创建任务时明确指定；对比期覆盖不足不做完整贡献归因。

两期均为闭区间、各1—90天，首版要求等长且对比期结束早于本期开始；自定义可不相邻，但报告披露节假日/季节差异，不能当已消除季节影响。不同长度、重叠或未来对比期在创建输入包时拒绝。现有calculate_review内部自行推算前一期，不能直接套用来计算自定义对比期：抽取接受显式两期的纯计算内核，旧入口适配后必须通过原样本回归。

### 4.2 新增 AnalysisInputBundle

数据组合在任务受理时固化：

~~~json
{
  "bundle_id": "<uuid>",
  "sales_dataset_id": "<owned-committed-dataset>",
  "sales_content_hash": "<sha256>",
  "inventory_dataset_id": null,
  "inventory_content_hash": null,
  "document_manifest_id": null,
  "document_manifest_hash": null,
  "period": {"start": "2026-09-01", "end": "2026-09-30"},
  "comparison_period": {"start": "2026-08-02", "end": "2026-08-31"},
  "metric_version": "diagnosis-v1",
  "sample": true
}
~~~

bundle属于当前用户与租户；所有绑定数据均在准入时校验所有权、确认状态及哈希，执行时再检查访问资格。数据版本不可修改，重复上传新文件不改变已受理任务。数据撤销访问后继续任务应安全失败，不用缓存绕过权限。

bundle记录允许来源与最大SKU/品类范围；任务sources是用户授权来源的非空子集，必须包含sales且不能重复。工具目录是bundle能力、task sources与服务端注册表的交集，不能仅因bundle含文档就让未授权任务调用文档工具。目录、摘要和缓存读取也执行相同权限检查。

库存扩展拟新增版本化CSV：sku、snapshot_day、available_units、lead_days；先限定单仓、单币种，SKU必须能与销售版本匹配，缺少提前期则不计算依赖提前期的风险。输入包注明库存快照与销售窗口，过旧/未来库存按配置检查；无库存不自动从旧products表补入另一时点的数据。

第一版每SKU一个固定snapshot_day，available_units为非负整数，lead_days允许缺失或非负整数；不扩展多仓/批次/采购在途。以明确窗口的paid_units/完整窗口天数作为“日均支付件数代理”，库存覆盖天数=available_units/该代理；它不是实际出库速率，也不能说明退款必然回库。日均为0、覆盖不完整或快照不适配时返回不可计算。只有lead_days和代理均有效，才给出available_units与lead_days×代理的比较；无企业安全库存规则时不擅自加安全系数。

规则文档清单固定document_id、version/content_hash、允许chunk身份与所属范围。新文档上传创建新清单，不原地替换既有清单。没有规则文档时可以完成销售诊断，但不能虚构企业阈值。

每个Evidence和ToolResult都带bundle_id与实际源哈希。跨工具一致性是“同一份固定版本组合”，不宣称销售与库存原始业务在同一数据库事务时刻采集。

### 4.3 确定性指标

- 净销售额 = revenue_cents - refund_cents，展示为货币，不能称利润。
- 净销售额变化贡献按SKU/品类加总对账到总体差额，前提是两期覆盖满足契约。
- 同期退款件数/支付件数只称记账期占比，分母为0时返回不可计算。
- 变化率的基期不为正时按既有约定不展示百分比，保留差额及原因。
- Top-K未覆盖的项目合为“其他”，完整明细留在证据账本，不能丢掉尾部后仍宣称总量对账完成。
- 无数据、明确0、不可比、输入不合法和工具故障分别编码；模型不能统一解释为“没有问题”。

贡献以有符号金额为主要事实，按绝对值排序展示增减项。“其他”=总体差额减已展示贡献，可正可负；总差额为0时不算贡献百分比，差额接近0时比例可能很大，也不强行压成0—100%。品类与SKU是两种单独分解，不能混合相加。计算用整数分/Decimal，展示四舍五入后不能用文本金额替代原始对账。

同一版本内商品品类固定；新上架商品按active_from计算预期覆盖，不把启用前无记录算缺失。当前元数据没有停用日期，启用后缺行仍为缺数，不能自行认定已下架。首版支持跨期退款记账，不宣称识别同一订单最终退款率。

手算基准：两期各1天，SKU A/B均在前期已启用且记录完整。金额单位为分：

| SKU | 前期支付额 | 前期退款额 | 本期支付额 | 本期退款额 | 净销售额变化贡献 |
| --- | --- | --- | --- | --- | --- |
| A | 100000 | 10000 | 80000 | 30000 | -40000 |
| B | 50000 | 0 | 70000 | 0 | +20000 |

总体净销售额140000→120000，差额-20000；支付额总体不变、退款额增加20000。Top-1若展示A，则“其他”为+20000，不能漏掉B的抵消。A占总体净下降的金额比例可为200%，不是“最多100%”。若两期支付件数分别均为150、退款件数为10和30，记账期退款件数占比约6.67%→20%；没有订单关联不能改称最终订单退款率。该数据只证明记账贡献，不证明退款发生的业务原因。

## 5. Agent图与停止机制

拟新增app/diagnosis/包，含schemas.py、metrics.py、tools.py、planner.py、graph.py、validation.py、reporting.py。它们为计划路径，当前不存在。现有app/research保留旧图，按profile路由。

~~~text
inspect_inputs
  ├─ 意图/对象需澄清 → request_clarification → 等待用户 → inspect_inputs
  └─ 可执行 → establish_baseline → propose_next_step
                                  ↓
                              authorize_step
                                  ↓
                              execute_one_tool
                                  ↓
                              assess_evidence
                    ┌─────────────┴────────────┐
                 仍有价值                  完成/预算/不足
                    ↓                         ↓
             propose_next_step          assemble_findings
                                              ↓
                                        validate_report
                                   修正一次 / 确定性降级
                                              ↓
                                  可选人工复核 → export
~~~

模型负责提出下一步：目标、待核验假设、一个允许工具及参数、预期得到何种证据。程序负责权限、参数、计算、预算、结果结构和停止裁决。首版每次执行一个工具，避免并行展开造成不必要预算与恢复复杂度。

### 5.1 图状态

至少包含task_id、bundle_id、graph_version、objective、scope、plan_version、steps、finding_refs、evidence_refs、open_questions、stop_reason、validation、report_version。图中只放有界摘要和引用，不堆全部CSV/文档正文。

每个step包含step_id、sequence、question、hypothesis_code、tool_name、validated_args、expected_evidence、status、call_id、evidence_ids、assessment、next_action。计划说明用于审查，不要求保存模型隐式思维链。

step状态固定为PROPOSED→AUTHORIZED→RUNNING→SUCCEEDED/FAILED/REUSED/REJECTED；停止/评估是步骤后的独立图决策，不反复覆盖执行事实。sequence包括被拒绝、失败和缓存命中步骤，最多8步；baseline工具单独标记，不伪装成模型自主选择。

假设状态：candidate、supported_observation、contradicted、insufficient。supported_observation只说明观察与证据一致，不等于因果已证明。例如“退款金额增加贡献了净销售额差额”可计算；“服务差导致退款”没有订单/原因标签就仍是待验证假设。

### 5.2 有界自主性

建议首版配置起点：每任务最多10次业务工具调用、12次模型请求、最多8个诊断步骤、报告修正最多一次。同规格成功结果只登记一份，后续命中直接复用；重复无信息行动仍受循环停止规则约束。执行时限和Token阈值沿用现有可配置机制，根据实测再调整；这些是拟配置值，不是已测最优值。

预算分配必须能对账：输入检查与基线最多占2次业务工具调用，最多8步各执行一个工具，共不超过10次。assess_evidence 的完整性/新增性检查由程序完成，模型对上步证据的评估并入下一次 propose_next_step，不能每个节点再额外调用一次模型。为报告生成、可选模型复核与一次修正预留模型额度；澄清理解、参数修正和失败重试同样消耗这12次全局额度，余额不足就提前收束，不能保证每次都执行满8步。人工复核不消耗模型调用，若触发模型改写仍按统一入口计数。

10次指实际获准执行的工具尝试，失败重试同样占次数；已落库成功调用的恢复不再次执行或扣次数。模型重复提出已成功规格时登记REUSED并占诊断step，不占新的实际工具次数，避免“缓存免费”变成无限推理。12次是实际模型请求尝试，全任务共享；摘要、结构化输出修正和评审同样计入。沿用max_retries=0，任何新增SDK/适配器不得隐藏重试。

新证据不是“又生成了一个ID”：按规范查询规格、源版本与事实键/值摘要判断新增性。完全相同摘要不算推进；预设问题的必要证据覆盖与允许停止条件由版本化任务类型规则检查。开放问题不宣称程序能完整判断已找到因果答案。

以下任一情况由程序停止：任务目标已覆盖；剩余问题需要不存在的数据；相同输入/输出反复循环；连续两个步骤无新增可用证据；预算耗尽；用户取消；无法确认模型费用；输入越权或版本不一致。

停止必须输出stop_reason与未完成问题。工具失败/预算不足时可交付degraded报告，但其事实与引用仍必须通过验证；无有效证据或关键事实无法校验则failed，不能为了“成功率”交付空报告。

LangGraph recursion_limit只是最后一层保护。现有Worker固定20，接入新图时需按有界最大路径计算新上限，另用持久步数/调用预算裁决，不能单纯提高recursion_limit解决死循环。

## 6. 工具契约

### 6.1 工具目录

| 工具（拟新增） | 参数 | 输出与用途 |
| --- | --- | --- |
| inspect_dataset | 不接受身份/任意路径；使用绑定bundle | 时间覆盖、质量、支持维度与不可计算项 |
| compare_periods | 白名单metric、授权filters；period来自bundle | 本期/前期/差额/可比性/事实目录 |
| rank_contributors | dimension=sku或category，top_k≤20，授权filters | 贡献项、其他合计、总体对账、完整证据引用 |
| drilldown_series | 指定授权SKU或品类，granularity=day | 日序列、缺失/零值、边界与聚合结果 |
| analyze_refunds | 授权filters | 支付/退款额及件数变化、分母效应，明确记账语义 |
| inventory_risk | 已绑定库存版本、授权SKU | 按明确算法/提前期计算，保留快照和数据缺口 |
| search_rule_documents | query≤300字符，limit≤5 | 固定文档清单内的片段、身份和逐字原文 |

工具参数由严格Schema验证，extra=forbid。SKU必须存在于bundle授权目录，时间不超出两期范围，指标不能由模型定义表达式，搜索不能指定外部dataset_id。身份、数据版本、文件位置、数据库连接由服务器注入。

不暴露通用SQL、Python执行、HTTP任意请求或文件读写工具。拒绝不可用工具时返回可理解的类型化错误，不把异常原文、SQL、请求头或密钥交给模型。

### 6.2 通用输出

~~~json
{
  "call_id": "<uuid>",
  "status": "ok",
  "bundle_id": "<uuid>",
  "tool": "rank_contributors",
  "tool_version": "1",
  "query_spec_hash": "<sha256>",
  "evidence_ids": ["ev-..."],
  "fact_catalog": {},
  "coverage": {"complete": true},
  "limitations": [],
  "next_dimensions": ["sku", "day"]
}
~~~

status限定ok、empty、incomparable、unsupported_metric、invalid_argument、unavailable；每类分别测试。响应有行数/字节上限，截断必须披露。完整明细存受权证据，模型只接收与本步相关摘要。

### 6.3 调用账本和复用

query_spec_hash包含task/bundle内容哈希、工具名/版本、指标算法版本、标准化filters、两期范围和top_k等影响输出的参数。不能直接复用旧metric_snapshot仅按name/period形成的键，否则不同SKU下钻会误取同一结果。

新增调用记录状态PLANNED、STARTED、SUCCEEDED、FAILED、UNKNOWN。step_id/call_id在外部调用前持久化；成功证据与调用结果同事务登记。只读确定性工具重试复用成功结果，失败尝试仍保留。跨用户禁止共享缓存；跨任务可先不共享，以免授权与版本混淆。

规范规格先写入tool_query_results，以UNIQUE(taskId,specHash)确定唯一成功结果；tool_invocations保存同规格的不同attempt，不能只在每次调用上分别声明唯一。调用开始事务同时校验租约/取消/预算、写STARTED并扣次数，成功事务同时写结果/Evidence和SUCCEEDED。中间崩溃时确定性工具可有界重算，实际新尝试计数；旧worker的迟到结果由fencing拒绝。

预算只在统一调用入口扣一次，避免新wrapper与旧ToolGateway._begin双重计数。特别注意：现有Repository.add_evidence内部还会按旧metric_scope快照去重，仅改新工具外层缓存不够。新诊断证据须使用包含specHash的新scope/独立写入入口，并保持旧profile去重不变；Evidence的source_key、payload、locator均纳入固定版本和规范规格。

## 7. 证据、假设与报告

每条数值事实绑定：evidence_id、metric_key、value、unit、entity_type/entity_id、period、comparison_period、bundle_hash、tool_version。事实由程序按引用渲染，不要求模型抄数和自行运算。

每条观察/假设绑定supporting_evidence、contradicting_evidence、missing_data、status和可核验的hypothesis_code。候选码与允许的确定性检验规则对应，例如sales_concentration、refund_amount_change、denominator_effect、daily_distribution、data_coverage。

证据校验至少包括：归属与权限、数据/算法版本、单位/实体/周期相符、贡献对账、引用原文存在、不得把占比变化改写为金额变化、不得断言未知因果、不得将已有证据说成完全缺失。

模型语义评审仅作为辅助，不覆盖程序事实，也不能作为报告正确性的唯一裁判。修正失败时使用已验证事实和明确缺口生成降级报告；没有有效事实则停止导出正式报告。

支持关系也要有实现约束：hypothesis_code对应确定性predicate与事实引用。例如refund_amount_change只能依据两期退款额差额报告方向；销售差额=支付额差额−退款额差额可以核算，但不能升级成服务/质量原因。模型提出候选，程序决定该候选是否满足支持规则并渲染观察文本；不能由模型自写“supported”再靠一次模型评审证明正确。

自由建议只允许表达待核对对象/资料/方式，不夹带新的已知事实或收益承诺。未能用结构规则验证的文档解读显式标为未核验解读，需人工复核后才作为确认解释展示；原文引用仍可直接核验。文档逐字命中只能证明引用存在，不能证明业务含义一定正确。
拟新增DiagnosticReport结构：

1. 任务范围与数据版本。
2. 经营事实及可比性。
3. 诊断过程摘要：实际执行了哪些步骤、获得什么证据、为什么停止。
4. 观察与候选解释：支持、反驳和不足分开。
5. 核对建议：对象、待补资料、预期验证方式；不承诺真实经营收益。
6. 限制、模型用量、版本与复核状态。

保留原commerce_review五章输出，不以新增章节破坏旧ReportSection Literal。新schema、模板和校验器单独路由，旧前端/导出回归必须通过。

## 8. 数据模型与API改造

### 8.1 拟新增/扩展

| 模型 | 最小字段与约束 |
| --- | --- |
| analysis_input_bundles | id、owner/tenant、销售/库存版本与哈希、文档清单、两期范围、metricVersion、sample；创建后不可变 |
| inventory_datasets | owner/tenant、confirmed、内容/文件hash、payload、createdAt；参照现有经营数据准入/配额 |
| document_manifests | owner/tenant、文档版本和片段身份清单、manifestHash；被引用清单不可原地改写 |
| diagnosis_steps | taskId、stepId、sequence、planVersion、问题/假设码、工具及规范参数、status、callId、assessment；UNIQUE(taskId,stepId) |
| tool_query_results | taskId、specHash、工具/算法版本、状态、resultDigest、evidenceIds；UNIQUE(taskId,specHash)，唯一规范结果 |
| tool_invocations | callId、taskId/stepId、specHash、attempt、预算预占、状态、evidenceIds、errorCode、耗时；UNIQUE(taskId,specHash,attempt)；关联唯一规范结果 |
| model_invocations | callId、taskId/stepId、phase、promptHash、modelVersion、attempt、状态、requestId、validatedOutput、usage、usageComplete；预算预占和调用身份同事务 |
| diagnostic_findings | taskId、findingId、hypothesisCode、status、证据/反证/缺口、reportVersion；关联必须属于同任务/授权输入 |
| input_requests | id、taskId、interruptType、questions/options、expectedTaskVersion、decision、resolvedAt；重复回答幂等 |
| task_feedback | taskId、actorId、findingId可空、评价类别、说明、createdAt；不自动覆盖原报告或训练模型 |
| Task扩展 | bundleId、parentTaskId可空、graphVersion、schemaVersion、promptVersion、toolRegistryVersion、pendingInterruptId、stateVersion；兼容旧profile，parentTaskId仅可引用本人任务 |

继续使用现有任务队列与Evidence/Artifact，不重复建第二套任务系统。新模块的写入同样受lease fencing保护，不能因为是“分析记录”绕过旧worker校验。反馈是用户自己的操作，另按会话权限和状态版本校验，不冒充worker写入。

### 8.2 请求与路由

TaskRequest新增profile=commerce_diagnosis、strategy=diagnostic；该组合强制绑定本人的已确认输入，sources不得超出bundle；不接受public_queries。保留旧research与commerce_review合法组合。

拟扩展示例：

~~~json
{
  "conversation_id": "<owned-conversation>",
  "query": "本期净销售额下降，主要涉及哪些商品，退款变化能解释多少？",
  "profile": "commerce_diagnosis",
  "strategy": "diagnostic",
  "bundle_id": "<owned-immutable-bundle>",
  "sources": ["sales"],
  "require_review": true
}
~~~

实际Schema须明确旧start_date/end_date与bundle的关系：诊断profile以bundle为权威，不能继承现有请求模型的静态日期默认值再误判冲突。用独立诊断请求类型，或用显式字段集合识别用户实际提供的日期；未提供时从bundle派生，明确提供却不一致则400，不静默覆盖。幂等请求哈希纳入bundle/版本，旧profile行为保持兼容。

| 拟新增接口 | 行为 |
| --- | --- |
| POST /api/analysis-inputs | 校验所有权/确认/两期，创建固定bundle；幂等 |
| GET /api/analysis-inputs/{id} | 本人查看来源、版本与质量，不泄露他人文档 |
| POST /api/commerce/inventory-datasets/preview、/{id}/commit | 库存扩展版本，整文件校验和配额 |
| POST /api/document-manifests | 从本人授权资料创建固定清单，核验文档版本 |
| GET /api/tasks/{id}/diagnosis | 计划、步骤、证据与停止原因，按版本分页 |
| POST /api/tasks/{id}/clarifications | 提交澄清答案，绑定interruptId和expectedVersion，重复请求幂等 |
| POST /api/tasks/{id}/feedback | 保存有界反馈，不改变报告真值 |
| POST /api/tasks | 复用现有202任务接口，增加新profile和CSRF/幂等契约 |
| GET events、stream、evidence、artifacts；POST cancel、review | 复用并扩展事件/报告投影，不新建平行认证系统 |

澄清与批准是不同动作，不能把开放问题答案塞进旧approved:boolean。所有变更请求沿用CSRF、身份和所有者校验；未登录401、不可见404、状态/幂等冲突409、非法输入400、限流429、服务不可判断503。

追问创建新task，记录parent_task_id并默认复用同bundle；首版复用问题上下文和输入版本，不直接把旧Evidence ID冒充新任务证据。需要旧事实时在新任务重算并登记自己的证据；旧模型结论只作为未验证上下文。换数据创建新bundle，不能悄悄修改旧报告。

澄清只允许选择已有授权SKU/品类、澄清歧义目标或缩小原任务范围，不改变bundle、周期、sources和预算。缺数据集/周期在任务创建前解决；执行后发现确需新增来源/换期，输出缺口或停止并引导创建新任务。不能为了“继续同一任务”修改已固定输入、复用旧缓存。答案按严格Schema校验并保存，不把用户自然语言直接拼成工具参数。

## 9. Worker、检查点与异常语义

现有Worker只处理require_review且有report的中断，新图接入必须同时修改profile路由、状态集合、interrupt识别、恢复、详情API与前端，不能只新增graph.py。

新增waiting_input状态和pendingInterruptId；保留waiting_review。等待时释放执行租约、不占运行中的worker槽，但计入活跃任务配额，设置可配置等待TTL并允许用户取消。等待不消耗执行时限；恢复时使用原已消耗预算与剩余执行时间，不重新获得一整份额度。达到等待TTL进入明确终态并保留输入请求。

同task_id使用同图版本和thread_id恢复，诊断图优先复用外层task线程；不随意增加被LeaseFencedSaver拒绝的线程前缀。若确需子图线程，先修改白名单与隔离测试，不能以放宽所有线程访问解决。

interrupt所在节点恢复时会重新执行；产生输入请求前的副作用必须幂等。用户答案先持久化并CAS更新等待状态，再允许唯一worker领取，通过Command(resume=...)恢复正确中断。重复/过期答案返回原结果或409，不能多次推进图。

等待状态与checkpoint可能分开提交。用稳定interruptId和input_requests唯一记录进行恢复对账：checkpoint已有中断而Task仍running时补写等待状态；答案已保存但尚未resume时重领后继续原中断。claim/finish、活跃配额、deadline计算、取消与重启扫描都要认识waiting_input；不把只更新API枚举当接入完成。等待TTL到期进入expired，不自动变成approved或重新补满预算。

当前Repository的ACTIVE与TERMINAL是显式集合：新增waiting_input进ACTIVE、expired进TERMINAL，同时更新API/前端状态联合类型和队列选择条件。用户答复只登记唯一可恢复决定，worker领取后才继续执行；API请求本身不直接推进图或持有执行租约。

新循环的step与Evidence提交、checkpoint提交并非天然同一事务。每个节点从调用账本恢复：已有成功call时复用，未登记请求结果时识别UNKNOWN。模型调用超时/进程被杀可能已经计费，不能自动当作未调用、零费用或反复免费重试。

模型调用也按稳定callId持久化：预算预占、STARTED和当前租约同事务；响应经Schema校验后，输出、usage与SUCCEEDED同事务提交。checkpoint未追上时复用该输出，不再次调用模型。STARTED无完整落库响应则视为UNKNOWN：保留已消耗次数并停止新模型调用；即使实际请求可能未发出，也不能凭猜测返还额度。需新尝试时由新任务或显式人工流程决定，首版不自动重试未知调用。

已收到但Schema不合法的响应，保存有界响应摘要/hash、usage和FAILED(validation_error)，与“没拿到响应、用量未知”分开；known usage仍须计入，允许的结构化修正是新的callId和新预算。不能先抛校验异常就丢失费用，也不能以修正覆盖旧调用记录。

不可在锁Task行期间等待模型或业务工具网络返回。现有RuntimeModelMeter与新账本共用一个预占入口，不能同时分别consume；已有usage_reports/model_calls接管检查要迁移到可对账的新记录，保持旧任务原计量语义。
已有lease token与检查点保护必须覆盖步骤、假设、报告和产物；旧worker写入全部拒绝。取消后不允许新增工具/模型调用；已经在途的只读调用可以结束，但不得提交新的有效报告。用户批准导出与worker执行版本绑定，旧草稿批准不能授权新报告。

取消与新调用以Task行上的预算预占/STARTED事务为先后界限：取消先提交则新预占拒绝；STARTED先提交的调用视为在途，即使网络发送稍晚也只能尽力中止，不能承诺物理请求瞬间撤回。在途调用保留用量/未知状态，取消不返还已发请求费用。旧worker不得借补报用量改写任务、报告或产物。

部署升级时已排队/运行/等待的任务固定graphVersion、schemaVersion、promptVersion。旧任务继续由兼容旧构建器处理，或显式取消并新建；不把旧checkpoint装进新图继续。迁移与回滚测试要包含waiting_input和waiting_review。

## 10. 安全、预算与运行

数据、文档、商品名称、用户任务文本和旧报告都视为数据，不能提升工具权限。注入样例必须测试“换用户ID”“读其他数据集”“忽略预算”“调用任意URL”等越权目标确实被代码拒绝，不能仅靠Prompt声明。

工具名单与schema在服务端固定，禁用通用代码/文件/SQL能力；检索结果不允许触发新增外部请求。引用和产物下载继续检查所有者，SSE游标不越过任务边界。

模型/工具次数在调用前持久化扣减，超额请求不发出。Token阈值沿用当前语义：依赖已返回usage在下一次调用前检查，不能称绝对费用封顶。未知usage标记不完整并停止后续模型调用；价格缺失时显示unknown，不显示0元。

记录模型/Prompt/工具/图版本、finish reason、重试、冷/热缓存、网络和提供方。成本估算区分模型、外部检索和基础设施，不把一个模型单价当整体成本。

诊断主链不引入业务写操作，人工复核是对报告导出/解释的确认，不是授予模型数据库写权限。前端不展示隐式思维链，展示有界计划、调用参数摘要、证据和结果判断。

产物先写带taskId/reportVersion/hash的临时存储键，再在受fencing保护的事务中登记Artifact并完成发布。取消/接管后产生的孤立文件不可下载，由清理器回收；不能先覆盖用户可见PDF再发现旧租约无效。报告版本与批准对象、Artifact名称/记录绑定，旧批准不覆盖新版本。
## 11. 前端交付

沿用浅色专业运营工作台，配置与报告分开。新增：

1. 新建诊断：问题、输入版本、本期/对比期、可用来源、可选复核；提交前看到缺数与不支持维度。
2. 执行过程：当前步骤、已完成证据、剩余预算、停止/降级原因；刷新后按持久事件恢复。
3. 澄清面板：显示具体问题与输入约束；用户回答或取消，不用“批准”代替所有输入。
4. 诊断报告：事实、支持观察、被反驳解释、未证实假设、缺口与建议分开；点击回到数据版本、工具结果或原文片段。
5. 假设卡片：显示证据数量与来源，不用模型主观confidence冒充统计置信度。
6. 任务对比与反馈：固定流程/Agent的同输入结果及成本对比，标明口径和样本；反馈不自动改写历史报告。

轮询和现有SSE均可按已实现接口使用；若前端新增SSE，必须验证重连、游标去重、权限和降级。不要因为后端已有stream路由就声称前端流式链路已完成。

## 12. 评测设计与放行门槛

### 12.1 分层证据

| 层级 | 能证明 | 不能证明 |
| --- | --- | --- |
| 确定性单元/属性测试 | 指标、过滤、对账、状态和权限不变量 | 真模型效果 |
| 脚本模型/替身协议 | 指定工具轨迹与失败分支可执行 | 模型能自主选择正确步骤 |
| 真实PostgreSQL恢复/并发 | 多worker领取、fencing、迁移与重放 | 生产可用性/模型质量 |
| 固定集真实模型 | 指定版本与输入的任务表现、轨迹、延迟和用量 | 全国通用效果/真实经营收益 |
| 浏览器真实API闭环 | 导入、任务、澄清、复核、证据与导出 | 未覆盖平台或外部服务的行为 |
| 真实业务人员核对 | 特定用户数据下的可用性反馈 | 广泛因果有效性或商业收益保证 |

本轮只交付文档，上述新版本验收均为UNTESTED。旧报告的通过条数不能移到新图。

### 12.2 新固定集

建议建立至少60条带唯一caseId的诊断用例：20条开发集、20条回归集、20条保留集。种子/输入哈希固定，保留集不参与Prompt调试；公开规则可以共享，但预期轨迹仅评估侧可读，不能混入模型上下文。

覆盖：正常/下降/退款分母效应/贡献抵消/基期非正/缺数/明确零/跨期退款/新增SKU/不支持利润与转化/时间歧义/多步必要性/冗余工具/文档冲突/权限注入/取消/未知usage/worker接管。关键小数据由独立手算或第二实现校验，不让同一函数既生成答案又验证自己。

每例记录：输入包、问题、可接受结论/禁止结论、必要证据、允许等价工具路径、停止条件、预期终态。不要要求唯一自然语言回答或唯一工具顺序；应按业务结论与证据判断。

### 12.3 基线比较

A：当前固定commerce_review；B：新单Agent诊断。比较共同支持的问题子集时，使用相同数据、模型、版本、业务答案要求和资源上限，分别报告成功率、证据正确率、工具数、模型数、Token、费用完整率及延迟。

对于固定流程不支持的动态下钻问题，单独报告新增能力覆盖，不能把“不支持”全部混成基线错误来制造提升。multi可作为C组后续实验，沿用同工具/输入，不提前假设提升。

真实模型至少选20条覆盖正常与高风险任务，每条重复3次，记录每次结果，不能只保留最好一次；最终放行可直接使用20条保留集完成60次运行。调用前明确提供方、预算上限和是否授权付费；本轮不运行。共同问题若B成本上升且效果未提高，应保留Workflow为默认，并说明适用边界。

问题模板、预期轨迹和场景标签不进入模型上下文；模拟商品名不能直接泄露“退款异常”真值。按case及场景分别报告：三次全对比例、单次运行正确率、合理拒答/缺口率、正向任务完成率和调用成本。共同问题用配对样本比较；预算允许上限相同，不要求两组实际使用相同工具数。60次重复不是60个独立业务问题，不能把重复运行当作扩大独立样本量。

### 12.4 拟定放行门槛

- 授权、版本、金额单位、贡献对账、预算硬限制、取消与旧worker写入：关键用例100%通过；任一反例FAIL阻止放行。
- 所有正式输出事实必须能核对；发现伪造数字、跨数据版本引用或无依据因果时，对应任务FAIL，不能用平均分覆盖。
- 回归与保留集业务任务完成率暂以≥90%作为迭代目标；这是目标，不是当前成绩。保留集20条的统计不确定性需披露。
- 真实模型至少60次运行全部保留，自动契约检查与逐条证据审查分别记录；模型自评不等于人工/独立核对。
- 受控循环必须在设定步数/预算内结束，所需数据缺失时正确停止也可算任务正确，不能算已经解决原因。
- P95/成本目标在G0测量后登记到版本化目标表；缺基线时不凭空写“降低50%”或“秒级响应”。
- live正向任务出现关键事实错误时停止该轮放行并保留失败；负向用例的预期拒绝不是正向失败。

门槛统计固定：每个split报告全部用例的预期行为正确率，以及仅对要求有效报告的正向用例计算的任务完成率，二者目标均≥90%；授权/事实/版本等关键负向门禁仍须100%正确。保留集重复运行同时给出case级三次全对与run级比例，主要门槛按预先标注的run级判定，不挑最好一次。降级/缺口只有符合该例预先定义的完成条件才算正确，不能把正常可答任务的拒答算成功。

## 13. 开发任务与验收

| 阶段/ID | 主要位置 | 交付与验收出口 |
| --- | --- | --- |
| G0 基线与问题集 | 当前graph/tools/metrics、evaluations、新需求说明 | 画出现有固定路径；保存同输入报告/证据/用量；建立caseId与手算小样本 |
| G1 版本与下钻 | 拟新增app/diagnosis/metrics.py、tools.py、schemas.py；现有datasets/models迁移 | 五个销售工具按固定版本正确运行；过滤不越权；贡献对账；复用键不串SKU |
| G2 诊断循环 | 拟新增planner.py、graph.py、validation.py；Worker按profile路由 | 能按问题选不同步骤；假设支持/反驳/不足；无效重复停止；脚本模型与真实模型证据分开 |
| G3 可靠继续 | repository、worker、checkpoints、步骤/调用账本、澄清API | step落库各崩溃窗口、旧token、取消、预算、澄清/复核重复恢复和未知用量正确 |
| G4 库存与规则 | 版本化库存/文档清单、inventory_risk、search_rule_documents | 本地可复现实例；快照时点明确；没有数据/规则不补造；文档注入无越权 |
| G5 产品闭环 | frontend/src、API、报告/导出 | 导入→诊断→下钻证据→澄清→复核→下载，刷新/重连/移动端可用 |
| G6 对照评测 | 新evaluations/diagnosis.py（拟新增）、cases、独立review记录 | 固定/Agent公平比较，60条用例与真实重复运行有完整失败与成本证据 |
| G7 运行交付 | Docker/目标环境探针、备份恢复、监控、学习与演示 | PostgreSQL多worker、可观测、备份恢复和限额演练；目标环境缺失则该项BLOCKED/UNTESTED |

G1可以与Java的判题改造并行。G2依赖工具契约；G3不能等上线后再补；G4/G5可分支推进但须复用同一输入包。两个项目共享工程知识，不强行共享数据库、身份或调用链。

### 13.1 各阶段具体要做什么

#### G0：复现已有能力并固定评测问题

1. 跑通旧固定复盘，画出请求、数据集、指标、模型、证据、复核与导出调用链。
2. 记录依赖锁、模型适配器、计量与检查点行为；保存同输入固定流程报告和用量。
3. 定义典型诊断问题、候选码/支持规则、数据/输出契约，建立独立手算小样本。
4. 建立开发/回归/保留集清单，检查泄露风险；提前定义共同问题与动态能力问题。

完成条件：旧能力可复现且证据范围清晰；指标答案有独立依据；能解释哪些问题需要多步诊断、哪些固定流程已足够。

#### G1：做正确的数据包和下钻工具

1. 新建AnalysisInputBundle及迁移，校验所有权、sources、两期范围、数据哈希和不可变规则。
2. 抽取显式两期的纯计算内核，支持SKU/品类过滤、日序列、退款分母与贡献对账。
3. 实现五个销售工具、严格输入Schema、固定规格哈希、唯一结果与调用账本；修正旧Repository内部快照去重。
4. 测试缺行/零/启用日期/跨期退款/抵消/自定义周期/缓存筛选/撤权，不接自由SQL或任意代码工具。

完成条件：GV01—GV05、GV21/GV22/GV26通过确定性与真实DB验证；同数据规格结果稳定，新查询不会错命中旧证据。

#### G2：实现有价值的单Agent诊断循环

1. 新建诊断请求/状态Schema与图路由，保留旧profile；实现基线、提议、授权、单工具执行、评估和停止。
2. 模型只选择下一步及结构化参数，程序判断候选支持规则并渲染事实/观察。
3. 加入8步/工具/模型预算、失败/缓存/无新增证据停止；明确为报告保留额度。
4. 用同表面问题不同中间证据的样例，证明路径和停止点会变化；加入脚本模型协议测试及小规模真实模型试跑证据。

完成条件：不同诊断任务有合理不同轨迹，GV06—GV09/GV17及GV27通过；脚本正确与真实模型有效分别报告。此阶段的新图在G3完成前不作为可恢复正式入口发布。

#### G3：让诊断能安全恢复和澄清

1. 扩展Worker/Repository/checkpoint路由，步骤、工具与模型输出均按稳定身份落库并受lease fencing保护。
2. 完成模型预占/usage/未知状态对账，与旧Meter统一入口；验证输出落库和checkpoint前后的崩溃窗口。
3. 实现waiting_input、澄清请求/答复CAS、等待恢复/TTL、配额和取消；保持bundle与预算不变。
4. 校验接管后的旧worker、重复答案/批准、进行中取消与版本升级，不能重新消费或导出旧结果。

完成条件：GV10—GV15、GV23—GV25有真实PostgreSQL恢复/并发证据；已成功调用不重复计费，未知调用停止，澄清不变相换数据。

#### G4：完成库存与内部规则扩展

1. 加入库存版本导入/确认和固定文档清单，与销售bundle统一来源身份。
2. 实现库存代理计算、提前期比较与不可计算规则，明确缺SKU、过旧快照和零需求边界。
3. 实现清单内规则检索、引用、冲突展示和未核验解读；遵守现有用户/租户文档权限。
4. 加入文档注入、跨来源权限、版本变化与撤权样例；不依赖外部RAG服务完成本地闭环。

完成条件：GV05/GV07/GV16/GV28通过；库存数字可手算、缺数据不补造，规则引用存在与语义确认分别记录。

#### G5：完成真实API产品闭环

1. 配置区选择数据/周期/来源，报告区展示诊断步骤、证据、已核验观察、缺口与停止原因。
2. 接入澄清、复核、追问新任务、反馈、取消、事件重连和产物下载；保持数据与报告版本可见。
3. 实现版本化报告/导出和临时文件发布，验证旧批准、孤立文件和权限边界。
4. 用真实测试API跑导入→诊断→澄清/复核→证据→下载；对无澄清正常路径和异常路径分别测试。

完成条件：GV13/GV14/GV18/GV25/GV29通过，刷新恢复和输出内容均经人工核对；不把全接口mock浏览器用例当闭环证据。

#### G6：证明诊断效果和代价

1. 完成至少60条用例及独立预期，开发集调试、回归集守回归、保留集只做最终评估。
2. 实现diagnosis评测CLI、完整实现指纹和轨迹断言，支持合理等价路径与预期停止。
3. 对共同能力做固定流程/单Agent配对比较；动态新增能力分开报告，保留全部重复、失败、成本与未知用量。
4. 执行最终20条×3次真实模型评估及逐条证据核对，按门槛决定默认入口，不预设Agent必胜。

完成条件：GV19/GV27及放行门槛有可复现记录；任何关键事实错误均保留并阻止该轮放行。未运行真实模型时该部分保持UNTESTED。

#### G7：形成运行与个人学习交付

1. 完成目标环境部署、依赖/配置探针、多worker、监控和资源限额说明。
2. 协调备份业务DB、checkpoint和产物，演练恢复、版本升级及取消/等待任务恢复。
3. 整理运行证据、演示数据与失败复现，区分模拟数据、用户声明真实数据和真实业务人员核对。
4. 本人逐步追踪并亲自修改下钻、图分支和恢复场景，形成源码地图与项目讲解材料。

完成条件：GV20/GV30及运行要求有真实证据和边界；另一开发者能复现，新增贡献与上游能力能够区分。

G2与G3需要交错验证，不能先用内存状态完成全部图设计再补持久化。G5可以先交付销售诊断界面，G4完成后接入库存/规则；最终验收仍包括G4。各阶段只按依赖推进，不设置日历或工时要求。

### 13.2 关键验收用例

| ID | 场景 | 预期 |
| --- | --- | --- |
| GV01 | 同一SKU/品类/日范围多次下钻 | 算法正确、证据版本一致，缓存不误取其他筛选 |
| GV02 | 两期差额正负贡献抵消且有Top-K | 包含其他项，合计到总体；不只展示下降项后夸大贡献 |
| GV03 | 退款占比升但金额降/持平 | 区分分子分母，不生成方向相反事实 |
| GV04 | 缺行、零值、基期0、无成本字段 | 分别解释；不补零、不算非法变化率、不输出利润 |
| GV05 | 同任务换数据/读取他人bundle/跨租户文档 | 代码层拒绝，无证据泄漏 |
| GV06 | 模型输出非法工具、额外字段或越界周期 | 校验失败并受限修正，不执行越权调用 |
| GV07 | 产品名/文档含忽略权限与预算指令 | 工具范围和计数不变，最终引用不把指令当业务事实 |
| GV08 | 同一问题有不同合理工具顺序 | 以证据与结果判定，不因顺序不同自动失败 |
| GV09 | 反复同规格工具、无新证据步骤 | 程序按停止规则结束，说明缺口 |
| GV10 | 模型超时无usage、硬杀后恢复 | 用量未知被保留，后续模型调用停止，不记零费用 |
| GV11 | 工具完成落库后、checkpoint前杀worker | 恢复复用同call/evidence，不重复有效记录 |
| GV12 | 旧worker在接管后写step/report/artifact | 全部受fencing拒绝 |
| GV13 | 澄清/复核回答重复、过期、他人代答 | 幂等或409/404；同任务只推进一次，权限不扩大 |
| GV14 | 用户取消时模型/工具仍在途 | 不再发新调用，不提交新有效报告，保留调用状态 |
| GV15 | 重启后恢复循环/等待、升级旧graph | 按原版本恢复；不丢已耗预算，不错误迁移checkpoint |
| GV16 | 文档冲突、库存过旧、销售SKU无库存 | 输出冲突/不足，不强行得到单一原因或覆盖天数 |
| GV17 | 伪造指标/引用，模型评审误判 | 程序门禁拒绝；不能靠评审器投票改写数值真值 |
| GV18 | 清空前端状态并刷新/重连 | 以持久任务/事件恢复，无重复步骤、越权游标或遗失报告 |
| GV19 | 同输入Workflow与Agent对照 | 区分共同能力与新增能力，保留所有重复结果/延迟/费用 |
| GV20 | 单独恢复业务DB、checkpoint、产物的一致备份 | 验证版本和hash，缺一类则恢复演练不通过 |
| GV21 | 两个筛选不同但旧metric_scope相同的查询 | Repository内部去重不误合并，新规格得到各自证据 |
| GV22 | 自定义等长前期、重叠/不等长周期、SKU启用边界 | 正常自定义按实际周期计算；非法输入拒绝；缺失与未启用区分 |
| GV23 | 模型输出/usage已落库但checkpoint未提交 | 恢复复用同call输出，不再次请求或扣次数 |
| GV24 | STARTED已落库、请求发送前/后分别杀worker | 均保守保留预占；未知不返还预算、不自动付费重试 |
| GV25 | checkpoint中断与Task等待状态错位、旧批准新报告 | 对账恢复正确中断；过期批准不能导出新版本 |
| GV26 | bundle含文档但task只授权sales、命中缓存时已撤权 | 仍禁止访问文档/旧缓存，不因来源存在扩大权限 |
| GV27 | baseline+循环+修正接近总预算、缓存循环、无信息新ID | 所有实际请求尝试有界；缓存步骤仍受步数限制，伪新增证据不能续命 |
| GV28 | paid_units为0/缺数、退款回库未知、库存lead_days缺失 | 不造覆盖天数/安全系数，不把支付件数代理称实际出库速率 |
| GV29 | 旧worker写文件后失去租约、用户取消或改报告 | 未登记临时产物不可下载、不覆盖已批准版本，可清理 |
| GV30 | 新旧profile/等待任务在数据库迁移与回滚中并存 | 版本路由、旧Meter、旧Report与新状态兼容，不能直接删除引用数据 |

## 14. 开发与验证命令

以下是当前已有命令，本轮未运行。先在隔离开发环境按README建立依赖，禁止拿正式业务库跑迁移演练/负载：

~~~sh
uv sync --frozen --python 3.12
uv run ruff check app tests migrations evaluations scripts
uv run ruff format --check app tests migrations evaluations scripts
uv run pytest -q
uv run python -m evaluations.commerce_review --mode demo --output evaluations/results/unique-baseline-run
uv run python -m evaluations.run --mode demo
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
pnpm --dir frontend build
pnpm --dir frontend format:check
COMMERCE_BROWSER_CHANNEL=chrome pnpm --dir frontend test:e2e
~~~

评测输出目录需要尚不存在，重复运行更换名称。临时pgserver需要可用依赖/本地端口；受环境限制时记录BLOCKED，不改测试绕开真实数据库。浏览器命令中的全部用例需要复核真实API与替身范围。

拟新增（G6完成后才可执行，不是当前可用命令）：

~~~sh
uv run python -m evaluations.diagnosis --mode demo --split regression --output evaluations/results/unique-diagnosis-run
uv run python -m evaluations.diagnosis --mode live --split holdout --repeats 3 --output evaluations/results/unique-live-run
~~~

G6实现CLI时须提供cases/split/model参数与付费预算确认，保留实现指纹和Prompt/数据/预期哈希。live命令不能自动扫描并使用任意凭据；模型端点由明确配置选择，日志脱敏。

迁移顺序：备份业务DB/checkpoint/产物→新建兼容表字段→旧任务按旧profile继续→新profile灰度→回归/恢复测试→启用新入口。回滚关闭新建诊断任务并排空/取消既有新任务；保留新证据和失败，不直接删除被引用输入。升级依赖单独提交并核对锁文件。

## 15. 最终交付、证据与学习

每批输出包含runId、commit/实现hash、依赖锁/图/工具/Prompt/指标版本、输入/预期hash、请求、计划步骤、实际工具调用、证据、报告、自动断言、独立核对、用量/费用完整性、延迟、失败和限制。新增模块纳入实现指纹，不能沿用旧评测仅覆盖旧文件的指纹范围。

任务摘要与工具轨迹必须能解释“为什么选择这一步、取得什么证据、为什么停止”。不提供隐式思维链；程序事件、工具输入输出和结构化判断已经足以审查。

运行指标包括排队/执行/人工等待、每工具耗时与错误、模型调用与未知用量、预算触发、循环停止原因、降级比例、证据错误、租约接管和旧写入拒绝。unknown费用单独统计，不混入平均零成本。

首版备份采用停写维护窗口：暂停任务创建/领取，等待在途执行结束或落成明确可恢复/UNKNOWN状态，再按同一backupId保存业务DB、checkpoint与产物清单/哈希。恢复只组合相同批次，校验模型调用账本与图版本后再开放领取；不拿三个独立时刻的备份宣称一致恢复。未来要在线一致备份时另做协议。

本次1.1复审核对了两SKU手算样例、贡献抵消/200%比例和2+8次工具、模型预留额度的算术一致性，并检查新增阶段与用例编号。这是文档/设计检查，不运行真实模型、数据库恢复或前端；上述30条新能力验收仍为UNTESTED。

完成定义：G0—G7有对应实现、版本、测试与证据；关键事实/权限/恢复门禁无未解决FAIL。外部服务或目标环境未提供时，明确对应BLOCKED/UNTESTED，不将本机替身/模拟数据等同于真实运营或生产验收。

本人学习交付：从一个问题追到请求、数据包、计划、工具、证据、报告和下载；亲自实现一个新下钻工具、一个图分支及一个故障恢复案例；能够比较固定流程与Agent的必要性、效果和代价。项目已有功能、上游功能、本人新增和仅计划部分分别说明。

## 16. 当前源码入口与官方参考

源码核查入口：app/research/{schemas,graph,tools,agents,evidence}.py；app/commerce/{datasets,review,service,hypotheses}.py；app/tasks/{worker,checkpoints}.py；app/storage/{models,repository}.py；app/api/server.py；evaluations/{commerce_review,run}.py；frontend/src；pyproject.toml与uv.lock。

现有说明重点：docs/01-architecture.md、docs/21-commerce-review-development-validation.md、docs/learning/01-总览.md。文档中的历史通过结果仅代表其记载版本和环境，需要查原批次证据，不自动继承。

- [LangGraph持久化](https://docs.langchain.com/oss/python/langgraph/persistence)：检查点与状态持久化的职责；不能替代业务调用幂等。
- [LangGraph中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)：同线程恢复及中断节点重执行；输入处理必须幂等。
- [LangGraph工作流与Agent](https://docs.langchain.com/oss/python/langgraph/workflows-agents)：固定流程和动态决策的区别；新图应通过实际工具轨迹证明。

官网示例可能随版本更新。实施按已锁定SDK核对，不直接采用网页中新API替换当前实现，也不把持久化本身称为exactly-once。
