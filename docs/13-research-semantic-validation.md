# 研究发现语义复核与综合用例复测

日期：2026-10-15。继续处理[上一轮](12-reliability-and-validation.md)综合补货用例中
F1将净销量与净销售额混淆、F8将互补规则误作冲突的问题。本轮不扩大外部检索、
多Agent或其他真实模型用例。

## 根因与改动

旧版`validate_synthesis`只能检查引用、逐字摘录和冲突来源数量，两个错误发现都能
通过。报告语义复核针对报告结论，不能替代对研究发现的复核。

- 将教学文档及两条历史反例保存在`tests/fixtures/research_semantics.json`，同时保留
  人工判错理由和修正样例。标签不是模型预测，不作为真实模型准确率。
- 新增`ResearchSemanticReview`。每个发现ID必须恰好出现一次，结果为supported、
  unsupported或uncertain，并附简短原文依据；遗漏、重复、未知ID和无效缺口索引均拒绝。
- 研究复核读取同一证据包中的文本与指标，核对金额/件数、跨句拼接、冲突的对象与
  条件，以及候选缺口是否与已取得资料矛盾。生成阶段仍只接收文本，报告阶段接收指标。
- 最多一次研究业务修正，输入原草稿、错误与完整评审；仍计入既有模型次数、Token、
  运行时间预算。结构化解析修正独立限次，不保证八次请求足够完成所有修正路径。
- 被拒绝的研究和复核保存为任务事件。最终仍未通过、复核关闭、超时或预算不足时，
  只向报告传递确定性原文摘录，研究标为failed，报告及产物记录降级原因。
- `research.json`及报告validation保留研究复核结果；评估分别检查research_execution
  和research_semantics。旧版仅有completed的记录不自动升级为语义通过。

复核仍使用同一个配置模型，可能产生相关性错误；supported只代表模型评审意见，
不能保证语义正确或取代人工核对。确定性数字绑定及引用校验继续执行。

实现前检查：已有摘录检查可复用，未发现研究语义复核的重复实现；沿用现有结构化调用
与预算边界，无新增运行依赖。接口用法核对了[Pydantic模型校验](https://docs.pydantic.dev/latest/concepts/validators/)
与[LangChain结构化输出](https://docs.langchain.com/oss/python/langchain/structured-output)，
并沿用已安装SDK及本项目报告复核的用法。根因有历史反例，实施置信度达到95%。

## 本地验证

- 后端全量：**112 passed，25.41秒**；真实隔离PostgreSQL16.2，包含5条PG测试。
- 固定demo评估：**32/32**；run_id `1455ce52-fb32-46bd-83d6-54807a992686`。
  仅为确定性本地评估，不是模型质量或生产延迟证明。
- Ruff、格式、Git diff检查通过。
- 新增反例、覆盖完整性、真实冲突/不确定意见、一次修正/持续拒绝、关闭复核、预算
  耗尽、超时脱敏及评估门槛测试。协议替身预置人工意见，验证流程如何处理意见，
  不证明真实模型能识别这些错误。

## 真实评估范围

用户授权仅一次综合补货评估，最多8次模型请求。评估器创建临时SQLite教学数据库，
读取仓库教学文档，调用配置的DeepSeek；不读取实际经营库或用户上传文件。
本地应用保持demo。原始记录保存在Git忽略的`evaluations/results/round4-20261015/`。

```bash
python -m evaluations.run --mode live --strategy single --case replenishment \
  --max-model-calls 8 --output evaluations/results/round4-20261015/replenishment-live.json
```

## FAIL：真实模型未进入研究阶段

本次run_id为`7c50f3dc-2988-4811-9b0f-ede852fc210e`，task_id为
`d41ce029-09f7-4164-b49a-5e7445de2a28`。配置为`deepseek-flash`、single策略、
本地教学文档检索，调用上限8次。

- 第一个规划请求在**25.280秒**后发生`APITimeoutError`，任务failed。
- 实际1次模型请求、0次工具请求，没有报告或研究产物；没有取得用量回执，
  `usage_complete=false`，不能把记录中的0 Token解释为免费或未产生服务商费用。
- `research_execution=false`、`research_semantics=false`、`agent_execution=false`。
  评估总结果为0/1，未将未执行的阶段计为通过。
- 人工核对了任务事件、检查项、产物缺失与用量状态；因没有模型研究结果，无法人工
  判断本次模型能否识别F1/F8，**新增真实语义复核效果仍未验**。
- 原始结果`replenishment-live.json`及人工失败说明留在上述忽略目录。未重试第二次
  付费评估，也未扩大其他真实用例。

实现快照SHA-256为`c75e6f302a98e24c7411cedc3274cd4cf912ab0d93f82af5bcf1481c5542f065`，
与本轮32条demo评估一致。该指纹覆盖app/evaluations代码及uv.lock，不含文档和测试。

## 未验与下一步

当前模型请求超时阻止了真实质量验收；不据此断言密钥无效或提供方宕机。代码当前
每请求超时为25秒，后续可评估将它改为可配置的值，再在单次授权预算内复测；
调整超时并不保证服务响应或质量通过。本轮没有通过放宽超时、跳过复核或增加请求
来取得绿色结果。

下一次真实验收应逐项核对研究statement/support、冲突是否互斥、候选缺口与实际
证据范围、报告四节及限制说明。不能把同模型评审意见当作独立事实证明。
Tavily/RAGFlow、真实经营数据、完整live用例集、多Agent收益及生产环境仍未验。
