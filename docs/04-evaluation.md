# 评估与真实服务联调

测试分为五层，分别记录结果：

1. 确定性业务口径、权限、文件边界、预算和数据归属。
2. 实际 LangGraph/Deep Agents 协议链：用脚本模型验证结构化输出、委派、只读工具、
   修正与回退，不评价模型智能或搜索质量。
3. SQLite 和隔离 PostgreSQL 的任务领取、检查点、人工复核和故障恢复。
4. 浏览器中的真实 API/Worker/React 操作及 PDF 文件下载。
5. 真实模型、Tavily、RAGFlow 的业务答案质量、延迟和费用。

## 固定离线评估

```bash
uv run python -m evaluations.run --mode demo --output evaluations/results/demo.json
```

32条用例覆盖经营、库存、文档、竞品、完整/部分/零记录、日期范围及信息边界。检查章节、
证据、报告中实际呈现的已知数字及其指标引用、各类来源是否在答案中使用，以及明确缺口。
只在检索结果中出现数字不能通过答案验收。首个正向失败后停止，并保留请求、任务、报告和证据。
demo 的毫秒级耗时是模板与本地工具耗时，不是大模型延迟或线上性能。

## 真实模型配置

在本地 `.env` 设置 AGENT_MODE=live、OPENAI_API_KEY、OPENAI_BASE_URL、LLM_MODEL，
以及 TAVILY_API_KEY。模型必须支持工具调用与 function-calling 结构化输出。
SEMANTIC_REVIEW=true 时还会发生一次或多次语义评审调用，计入模型预算。
接口兼容不保证模型行为相同，应逐个验证实际使用的模型。

公开关键词独立填写，每条最多160字符，最多3条。内部经营数据与文档不会被 Gateway
自动复制到公开搜索参数。公开搜索返回的片段不代表系统已经读取完整网页。

本地内部检索是小语料的词法/BM25式基线。若要使用 RAGFlow，配置：

```dotenv
KNOWLEDGE_PROVIDER=ragflow
RAGFLOW_API_URL=https://your-ragflow-service
RAGFLOW_API_KEY=your-local-secret
RAGFLOW_DATASETS={"demo":["allowed-dataset-id"]}
```

这里的组织键必须与实际账户组织一致，production 初始账户为 primary。禁止使用
不受组织范围限制的“查询所有知识库”。适配器检查 dataset_id/kb_id，保留内容与
定位；RAGFlow 未返回页码时不伪造页码。需在实际服务版本上验证字段与引用位置。

## 固定流程/单/多 Agent 对比

```bash
uv run python -m evaluations.run --mode live --strategy workflow --output evaluations/results/live-workflow.json
uv run python -m evaluations.run --mode live --strategy single --output evaluations/results/live-single.json
uv run python -m evaluations.run --mode live --strategy multi --output evaluations/results/live-multi.json
```

在同一模型、预算、业务数据与文档下重复至少3轮。公开搜索可能随时间变化，应保存
每轮证据快照；若未冻结外部来源，不能将差异完全归因于架构。demo 不运行真正委派，
因此 demo single/multi 对比不能用于证明多 Agent 效果。

评估器每条用例重新建立模拟经营fixture，真实模式表示调用真实模型/外部服务，不表示
使用真实公司的经营数据。结果保留report、evidence、events、research与数据集/样例/
实现（含评估代码与uv锁）/配置指纹。来源未冻结时不能完全归因于架构。

每种策略保存至少3个不同完整轮次；例如live-workflow-r1.json至r3.json。对每份结果生成
空白评分表，填写正确性、证据支持、完整性、工具行为（0/1/2），未满分项说明原文依据。

```bash
uv run python -m evaluations.review template evaluations/results/live-workflow-r1.json --output evaluations/results/labels/REPLACE_RUN_ID.json
uv run python -m evaluations.review compare evaluations/results/live-*-r*.json --annotations evaluations/results/labels --output evaluations/results/comparison.json
```

标签文件名必须为结果文件中的run_id（UUID），将REPLACE_RUN_ID替换为实际值。
人工表只保存评分与评语，原始答案、逐字引用及研究发现从对应结果JSON核对。
标注绑定结果文件SHA-256，修改原结果后要重新核验。比较器拒绝demo、缺标签、未完成
轮次、重复run_id、不同数据集/代码/模型/预算/价格设置；未配置Token单价显示费用未知。
它统计人工分、契约通过率、回退率、真实委派率、P50/P95、调用与Token；这些统计不
证明显著性或真实商业收益。对比工具的协议测试使用合成结果文件，不属于质量实验。

人工逐项核对：经营表现、库存风险、竞品观察、行动建议、信息缺口、关键数字和引用。
为每项结论标记正确/错误/证据不足，并说明片段为什么支持或不支持结论。
规则校验与模型评审仅辅助人工判断；不得把“有引用”作为答案正确的唯一依据。
live 评估还检查模型是否实际完成草稿、是否使用回退、用量是否完整、是否完成配置的
语义复核。系统通过确定性回退完成报告，不能算成模型研究任务成功。

## 费用与失败记录

可设置 INPUT_PRICE_PER_MILLION / OUTPUT_PRICE_PER_MILLION，单位须与提供方价格表
一致。API 的 estimated_cost 是模型 Token 费用估算，不包括检索/搜索或服务器费用。
提供方没有返回完整用量时不估算，并停止后续模型调用，避免未知用量继续累积。

不要将失败日志中的真实密钥或完整客户材料上传仓库。结果保存在被忽略的
evaluations/results，发布可脱敏的验收结论及必要失败样本。

真实联调入口已经实现，但本次未配置相关密钥；最新验证结果见 `09-validation-round2.md`。
