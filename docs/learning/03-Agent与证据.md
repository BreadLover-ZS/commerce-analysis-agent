# Agent与证据：流程、权限和校验

前置：[后端主链](02-后端主链.md)。先理解固定经营复盘，再选读高级研究；“用了模型”和“执行了自主 Agent 循环”要分清。

## 1. 先看图的边，再看节点

打开 [graph.py](../../app/research/graph.py) 的 `build_workflow`，从末尾 `add_node/add_edge/add_conditional_edges` 开始：

```text
plan → collect → draft → validate → review → export
                   ↑       │           └→ 驳回结束
                   └─ 未通过
```

`ResearchState` 是图内状态字典，节点返回增量；本图没有给列表字段配置累加 reducer，不能假定列表自动追加。

| 节点 | 核心责任 | 主要结果 |
| --- | --- | --- |
| plan | 根据任务来源与权限确定计划 | plan、gaps |
| collect | 计算指标、采集资料、可选研究与复核 | evidence_context、research、research_validation |
| draft | 生成固定或模型草稿，处理修正反馈 | report、draft_attempts、回退标志 |
| validate | 指标与章节检查、可选语义评审、保存报告 | report、validation |
| review | 可选人工中断，接受批准或驳回 | approved、status |
| export | 写报告与证据文件，登记产物 | status |

给每个节点补一句“读哪个键、写哪里、失败留下什么”，不必通读整个长函数。

## 2. 默认经营复盘：程序事实与受控假设

`collect` 读不可变数据版本，得到确定性指标和贡献分解；`deterministic_report → commerce_review_report` 生成锁定的事实与变化观察。

live 的 `draft` 使用 [hypotheses.py](../../app/commerce/hypotheses.py) 的 `allowed_hypotheses` 与 [schemas.py](../../app/research/schemas.py) 的 `ReviewNarrative`：模型只选允许的假设代码并给核对建议。数据能证明下降了多少，不能直接证明广告、价格或流量导致下降。

`validate_report` 继续检查固定事实章节、观察前缀、候选假设、数字和已知方向反例。合法结构不等于业务判断成立；事实、变化观察、候选原因和建议应分别辨认。这个固定入口不进入 Deep Agent 自主检索，也不能据此推导多 Agent 收益。

## 3. 高级研究：什么时候进入 Deep Agent

沿 `graph.collect` → [agents.py](../../app/research/agents.py) 的 `create_researcher` → [tools.py](../../app/research/tools.py) 的 `ToolGateway` 阅读。

| 策略 | 采集方式 | 边界 |
| --- | --- | --- |
| workflow | 按授权词由程序调用工具 | live 仍可能用模型规划、归纳、生成与评审 |
| single | 满足条件后进入 Deep Agent | 关闭默认 general-purpose 子 Agent |
| multi | 同上，配置允许来源的专家 | 模型未必真正委派，更未必有收益 |

进入条件同时要求：存在模型、策略不是 workflow、计划包含 web 且有 web_queries。纯指标或只有内部文档时，选 multi 也不表示执行了专家协作。

multi 可配置 `market_researcher/operations_researcher`。核对 delegation 事件、工具结果和终态，区分“配置专家”“发出委派”“完成执行”“取得收益”。

`create_researcher` 准备可信 Skills、租约工作目录、虚拟文件 backend、model profile、受限检索工具及专家。文件只开放 `read_file/ls/glob/grep`，另拒绝写操作；没有 Shell、自由 SQL 或任意 URL 抓取。这不是独立操作系统安全沙箱。

## 4. Skill 给指南，Gateway 执行权限

读 [commerce-review Skill](../../app/skills/commerce-review/SKILL.md) 与 [competitor-research Skill](../../app/skills/competitor-research/SKILL.md)。它们指导口径，权限仍由程序强制检查。

- `RunContext` 由服务端绑定身份和租约，模型不能传 tenant_id 扩权。
- `ToolGateway.web` 要求 query 逐字属于用户填写的 public_queries，不能把内部段落临时变成公开搜索词。
- local 检索先过滤组织规则和私有附件，使用词法打分，不是向量检索或 reranker。按 1000 字符步长取最多 1200 字符块，通常重叠 200 字符；授权片段超上限明确拒绝，不静默漏搜。
- RAGFlow 返回组织资料原始片段，并组合本地当前用户/会话附件，不做嵌套问答。可记录的远端故障保留附件及缺口；越权数据集或来源身份缺失则拒绝。
- 外部文本中的“忽略规则”“执行命令”属于数据，不能改变权限。

附件解析检查格式、大小、页数、解压后文本等，在限时子进程中处理，Linux 另有资源限制。只保存提取片段与位置，不保存原始附件字节，不能宣称已具备原文件归档。

## 5. 每次模型请求都要经过预算边界

读 `agents.structured_call` → [meter.py](../../app/research/meter.py) 的 `RuntimeModelMeter` → `Repository.consume/record_usage`。

结构化调用要求目标 Schema。直连 DeepSeek 有专门 JSON mode、非 thinking 与超时配置，以锁定依赖和代码为准。解析失败可修正一次；`max_retries=0` 只关闭 SDK 自动重试，项目仍有显式修正请求。

| 时点 | 行为 |
| --- | --- |
| 请求前 | 检查租约、用量未知、次数与 Token 阈值，登记请求 |
| 成功后 | 校验回执后累计；覆盖 SDK 内部摘要调用 |
| 异常/缺回执 | usage_complete=False，阻止后续新请求 |
| 恢复时 | 已登记但未回执的请求按未知处理 |

回执只接受范围内非负整数，拒绝 bool、字符串、小数和溢出；晚到回执不能让未知状态重新变可信。模型 callback 的准入失败必须能阻止实际调用。

Token 按已返回用量在下次请求前检查，单次可能跨阈值；在途请求无法撤销，不能承诺绝对费用封顶。缺单价或完整回执不估算确定费用。读取/连接超时与任务总截止时间要分开。

## 6. 证据账本、上下文、研究、报告四层

```text
工具结果 / 文本片段
  → add_evidence：账本
  → evidence_package：选来源和窗口，保留 manifest
  → ResearchSynthesis：发现、冲突、缺口，单独复核
  → ResearchReport：事实 / 分析 / 建议，单独校验
  → 可选人工复核 → 导出
```

有序入口：[storage/evidence.py](../../app/storage/evidence.py) 的 `authoritative_evidence/document_identity` → [context.py](../../app/research/context.py) 的 `evidence_package` → [synthesis.py](../../app/research/synthesis.py) 的 `reviewed_synthesis` → [evidence.py](../../app/research/evidence.py) 的 `metric_binding_issues/render_bound_metrics/validate_report`。

**账本**保存实际取得且有长度限制的片段、定位、payload 和哈希，未必是完整原文；网页通常只有搜索片段。哈希证明保存内容一致，不证明来源可靠。

**上下文包**优先选指标，再按相关性和来源轮转选文本，控制来源数与字符数。库存包裁剪展示条目，完整结果留在账本。文档配额用 provider/dataset/document 身份计数，不能按页码 locator 区分文档；旧身份推断与未知身份显式披露。manifest 记录选中/排除 ID、原因、窗口和截断；账本存在某片段不代表模型看过它。事实采用首份权威指标。

**研究复核**有两道门：程序要求 quote 是 excerpt 的逐字子串、ID 合法，冲突至少引用不同 locator；模型再对每项发现恰好返回 supported/unsupported/uncertain。漏 ID、重复、未知 ID 或 uncertain 都不能算通过。逐字摘录存在，也可能被误拼成因果或冲突。

研究另收 business_fact_index，知道整个任务已有的事实键，避免因文本没写库存就声称任务没有库存；它不允许伪造数值或 quote。首次失败最多修正一次，再失败只保留确定性摘录和失败标志，不能把被拒研究当成核准结论交给 draft。

**报告校验**区分三类 Claim：

| kind | 用途 | 指标规则 |
| --- | --- | --- |
| fact | 可核对事实 | 完整 MetricReference，程序渲染指标句 |
| analysis | 解释已有事实 | metrics 为空，用 evidence_ids 支持 |
| recommendation | 待确认建议 | metrics 为空，事实前提仍需依据 |

指标绑定核对 evidence 类型、键、值、商品、单位、周期和快照，非法引用不能静默改对。把包的 180 天写成杯的覆盖应拒绝。工具配置提前期需对应事实目录；文档“60 天”用文本引用，不能冒充经营计算指标。

程序还查章节、引用、重复 ID、自由数字和模拟标记。模型报告评审检查支持关系及限制，但会误判；“评审执行过”不等于事实正确。“未展示某指标”也不能改成“该指标不存在”。

## 7. 修正、降级与人工恢复

| 层次 | 次数或后续行为 |
| --- | --- |
| JSON/Schema 解析 | 首次 + 一次结构修正 |
| 研究发现 | 首次候选 + 一次业务修正，仍失败只保留摘录 |
| 报告校验 | 最多两版草稿，再回退确定性模板 |
| 确定性模板仍失败 | report_validation_failed 终止，不能无限循环 |

草稿生成异常的 draft_fallback_used 与校验后模板回退的 fallback_used 分开看。报告模型评审不可用时，可在程序校验通过后带限制交付；真实评估器可能仍判失败。研究、报告、任务状态各有门槛，嵌套修正也意味着任务可能不止两次模型请求。

人工复核链：`graph.review: interrupt` → Worker 登记 waiting_review → API 保存决定并排队 → Worker 用 `Command(resume=...)` 恢复 → 批准导出 / 驳回结束。恢复会重新进入中断节点，interrupt 前不能放不可控的重复副作用。

`snapshot.next` 为空不一定代表 pending writes 已归并完成。已有状态通过 `ainvoke(None, durability="sync")` 恢复，真正完成的图不重跑已完成步骤；同步检查点仍不能消除节点内部外部调用窗口。

接着完成 [实践与自测](04-实践与自测.md)，从报告反查指标、研究与原文，再判断分析有没有越过证据边界。
