# v15真实验证部分结果（2026-10-26）

完整结论见[50号报告](../../50-v15-formal-validation-budget-stop-20261026.md)。用户明确授权96 Task及向DeepSeek发送冻结合成业务数据，累计上限保持1200万；实际75/96后预算发送前停止。自动72/75、作者侧AI审查72/75、交集72/75；H14—H20共21份未执行，G6未放行，G7排除。

|材料|用途|
|---|---|
|authorization-20261026.json、launch-check-20261026.json|具体目的地、数据、96 Task和累计上限授权及启动预检|
|destination-authorization-required.json|启动前一次自动审批阻断的历史记录，后来已获用户明确目的地授权；该次无请求|
|summary.json、review-result.json|原始自动汇总、已有75份逐份审查及交集|
|review-provenance.json、review-decisions.json|作者侧AI身份、每份判断、未执行范围；不是人工/组织独立验收|
|per-receipt-results.csv|75份原始回执hash、自动/审查结果及失败分类|
|failure-classification.json|固定H03两份容量异常、H13发送前预算拒绝/UNKNOWN误分及21份未执行|
|H03-fixed-claim-capacity-root-cause.json|已有响应4/4/3条假设→9/9/8条章节的零付费复算|
|author-display-audit.json|402项必答值及逐日展示核查；18个缺项来自无报告，6条日值因预算尚未查询|
|standard-audit.log|标准审计因Task UNKNOWN拒绝，原始结果未修改|
|read-only-audit-summary.json|135个HTTP、原始行评分及journal的独立只读核对，明确不能替代Task账本修复或允许继续扣费|
|receipt.json、SHA256SUMS|最终状态、用量、原始hash及本目录完整性|

完整原始数据位于`evaluations/results/diagnosis-v15-h01-h20-live-20261026-01/`：75份Task与每次输入/预期、135个HTTP响应、模型和工具账本、journal、报告产物、完整只读审计、审查及分析脚本。原始目录是证据组成部分，摘要不能替代它。

累计10,954,662/12,000,000 token、1576次HTTP。全局HTTP无未知用量，未结算预占0；Task账本有1条UNKNOWN且标准续评受阻。禁止据此手工消除UNKNOWN、提高额度或额外付费重试。
