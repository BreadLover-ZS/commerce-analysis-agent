# 02 模块 · Deep Agents、工具与预算

前置：[流程篇](05-LangGraph流程与人工复核.md)。业务问题：Agent 可以决定什么，程序必须控制什么，多 Agent 是否真的发生了？

## Part A：把几层调用拆开

| 层次 | 当前入口 | 决定什么 |
| --- | --- | --- |
| 确定性主流程 | [graph.build_workflow](../../../app/research/graph.py) | 阶段顺序、授权检查、修正、复核与交付 |
| 结构化模型调用 | [agents.structured_call](../../../app/research/agents.py) | 生成计划、研究发现、报告或评审对象 |
| 自主研究循环 | `agents.create_researcher` | 在开放工具内选择检索、读 Skill、可选委派 |
| 业务工具边界 | [tools.ToolGateway](../../../app/research/tools.py) | 身份、授权、调用计数、结果证据化 |
| 模型请求边界 | [meter.RuntimeModelMeter](../../../app/research/meter.py) | 请求准入、回执、延迟与安全错误 |

本项目的 Agent 不是获得一个问题后自由执行系统命令。模型只能选择已暴露的工具及参数，工具执行仍由程序控制。

## Part B：三种策略与真实进入条件

| 策略 | 采集方式 | 当前实现需要核实的点 |
| --- | --- | --- |
| workflow | 程序按授权词调用工具 | live 仍可能调用模型规划、归纳和生成报告 |
| single | 满足条件后使用 Deep Agent | 关闭默认 general-purpose 子 Agent |
| multi | 同上，按允许来源配置专家 | 是否实际委派由模型决定 |

条件是 live、有 web 来源与公开查询、策略不是 workflow，详见 collect 的 `if`。三个真实库存/规则/综合教学单例不等同于完整三策略收益实验，也不能据其证明专家协作提升质量。

`create_researcher` 中，multi 可配置 `market_researcher` 和 `operations_researcher`，各自只获得对应检索工具。`DelegationMiddleware.awrap_model_call` 看到名为 `task` 的工具调用时写 delegation 事件。配置专家只是具备能力；还应结合事件、工具结果和终态确认实际执行情况。

官方[子 Agent 说明](https://docs.langchain.com/oss/python/deepagents/subagents)用于理解上下文隔离和委派；本项目具体默认值、权限与工具集以锁定依赖和 `create_researcher` 为准，不照抄文档最新示例替换现有代码。

## Part C：逐行读 `create_researcher`

1. 将可信 `app/skills/` 拷到本次租约的工作目录。
2. 建立 `FilesystemBackend(root_dir=workspace, virtual_mode=True)`。
3. 同时设置 provider 与具体 model profile，禁用默认通用子 Agent。
4. 从 `gateway.research_tools()` 取得允许的检索工具。
5. 设置业务 policy，按 strategy 配置专家。
6. 文件 middleware 只开放 read_file、ls、glob、grep，权限再拒绝写操作。
7. 将模型、检查点、只读文件能力、Skills 和业务工具交给 `create_deep_agent`。

这里没有给模型 Shell、任意 SQL 或任意 URL 抓取能力。工作目录与只读策略控制文件访问，但不应称为独立操作系统安全沙箱。

## Part D：Skill、Prompt 与 Gateway 各自负责什么

阅读[经营复盘 Skill](../../../app/skills/commerce-review/SKILL.md)与[竞品研究 Skill](../../../app/skills/competitor-research/SKILL.md)。这些文件告诉 Agent 遵守口径、区分事实建议、保留来源；它们是可信业务指南，不是授权系统。

提示中说“不要泄露内部资料”还不够。`ToolGateway.web` 强制 query 必须逐字属于用户提供的 `public_queries`，端点由程序固定，模型不能把检索到的内部段落换成公开查询词。

内部文档检索同样由 Gateway 绑定 `RunContext`。local 模式查询授权组织规则与会话附件；RAGFlow 模式将该租户远端数据集与本地私有附件组合，一次工具准入覆盖两个受控查询。接口返回原始片段及来源身份，不执行嵌套问答。切换组织知识提供方不能让已上传附件消失，也不能放宽附件归属。

外部材料是待分析数据；其中“忽略规则”“执行命令”等文字不能改变工具权限。防护不能只依赖模型理解提示。

## Part E：结构化输出为什么还需要修正和校验

`structured_call` 调用 `with_structured_output`，让模型返回目标 Schema。默认使用 function_calling；直连 DeepSeek 时用 JSON mode，并把 Schema 约束加入请求。`model_from_settings` 对该直连域名配置非 thinking 模式、读取/连接超时、`max_retries=0`。

解析失败最多进行一次结构修正，诊断只保留字段路径和错误类型。合法 JSON 不等于业务正确：未知引用、错商品和错误推理仍需后续检查。SDK 自动重试被关闭，也不代表项目没有显式修正请求。

## Part F：预算在模型边界计数

按 `attach_meter → RuntimeModelMeter → Repository.consume/record_usage` 阅读。

| 时机 | 动作 | 解决的问题 |
| --- | --- | --- |
| 模型请求开始前 | `consume(ctx, "model")` | 检查租约、未知用量、次数和 Token 阈值，再登记请求 |
| 请求成功后 | `record_usage` | 校验输入/输出计数后累计 |
| 回执缺失或调用异常 | 标记 `usage_complete=False` | 不把未知当作零成本，阻止后续新请求 |
| 进程恢复 | `account_unreported_usage` | 发现已登记请求却没回执时按未知处理 |

Callback 的 `raise_error=True` 使准入失败能阻止实际调用。计数还覆盖 SDK 内部摘要请求，不能只在 draft 外面加一行计数。

回执必须是非负整数且不超出存储范围；bool、字符串、小数和溢出不采信。未知标志不会因为晚到一份有效回执而恢复可信。

模型次数、业务工具次数是调用准入限制；Token 预算按已返回用量在下次请求前检查，单次请求可能跨过阈值。已有在途请求也无法撤销，所以不能承诺绝对费用封顶。未配置单价或回执不完整时，不估算确定金额。

## 验证入口

```bash
uv run pytest -q tests/test_live_protocol.py::test_real_harness_and_structured_output_with_protocol_double
uv run pytest -q tests/test_live_protocol.py -k 'summarization or missing_usage'
uv run pytest -q tests/test_usage_validation.py
```

这些测试使用真实 SDK 与脚本模型/协议替身，不调用付费模型。先看 [ScriptedCommerceModel](../../../tests/fake_model.py) 怎样返回 tool_calls 和用量，再看测试如何核对 Skill 加载与委派。脚本评审返回 supported 只证明协议可达，不证明语义能力。

## 关键问题

1. 没有 web 来源时，选择 multi 会执行什么路径？
2. 让模型参数新增 `tenant_id` 为什么不是合理的权限扩展？
3. Skill 被模型读到，为什么仍需要 Gateway 的强制校验？
4. 为什么“SDK 不重试”和“一个任务只请求模型一次”是两回事？

<details>
<summary>判定要点</summary>

应回到 collect 条件、RunContext、公开词逐字白名单和三层修正；能指出 callback 的请求准入以及 SDK 内部调用。谈 multi 时需区分“配置”“产生委派调用”“完整执行”“获得质量收益”四件事。

</details>

下一篇：[证据、研究与报告校验](07-证据研究与报告校验.md)。
