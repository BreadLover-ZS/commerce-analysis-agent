# v14正式轮结果摘要（2026-10-25）

完整结果见[47号验收报告](../../47-v14-formal-acceptance-20261025.md)：96/96已执行，原冻结自动83/96，严格作者审查91/96，共同通过80/96；G6未放行。

|材料|用途|
|---|---|
|summary.json|原自动汇总副本，含预算及36组配对条件|
|review-result.json|原冻结自动评分与逐份审查的交集、门槛和未放行原因|
|grading-audit-summary.json|96份再评分逐项一致、源码和账本审计摘要|
|review-provenance.json|作者侧AI审查身份/范围；不是组织独立或人工验收|
|review-decisions.json|逐题审查意见及全部96份PASS/FAIL和失败准则|
|per-receipt-results.csv|全部96份原回执hash、自动/作者审查/交集与分类|
|failure-classification.json|13条自动FAIL：2条真实漏答、3条等价路径争议、8条gap停止冲突；另3条固定作者展示缺口|
|author-display-audit.json|462项作者数值直接展示检查，保留9个缺项；不替换原评分|
|receipt.json|源码、执行/审查、预算、原包保护及文件hash汇总|
|SHA256SUMS|本目录材料完整性|

自动评分、作者审查和原始Task分开保存。H03作者九项展示义务强于题面核心差额，严格审查结果必须带此口径限制；不能只取某种分数作为通用模型能力。

完整原始证据在忽略目录 evaluations/results/diagnosis-v14-h01-h20-live-20261025-01/，包含cases-source、每次输入/预期、96份Task、工具/模型/HTTP/journal、报告产物、完整grading-audit及codex-evidence-reviews。仓库摘要不能代替这些原始材料。

本轮新增153次HTTP、1,155,229 token，累计9,768,379/12,000,000、1441次HTTP，UNKNOWN0。没有额外付费重跑或修改评分。默认经营复盘入口保留，G7排除。
