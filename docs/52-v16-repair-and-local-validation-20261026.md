# v16修复与本地验收（2026-10-26）

按[51号方案](51-v16-repair-plan-and-confidence-gates-20261026.md)及用户确认实施P1—P4：报告容量、两条调用入口计量、历史安全对账和本地集成验证。G7继续排除；不调用真实模型、不增加累计额度、不改原始账本与正式成绩。

最终源码：`99530b7b82574e9e610a657f330199c3f21508c7b0d1305d3f9b0048274be62b`，244个实现文件。Git基线仍为`62d98051982c4a8a277df6cb88fe37030185241a`；本轮没有提交或推送，已有v15未提交工作一并保留。

**代码及本地修复完成；交付指纹完整PostgreSQL 1059 PASS、0 FAIL、0 SKIP。Chrome串行19 PASS、5/5缺陷注入检出。历史对账明确BLOCKED，v16真实模型效果UNTESTED，G6未放行。**

## 1. 修复结果

|问题|原来的失败|现在的行为与边界|
|---|---|---|
|P1 报告容量|合法4条假设加到5条程序说明，9条超过8条，validate节点抛错而无报告|固定v5先完整校验提议、动作和对象范围；在独立副本中分配余量，保护程序事实，公开说明可选解释省略，完整重验后才提交；非法第4项不能靠裁剪隐藏|
|P1 回退与复核|已验证对象的列表追加绕过即时校验，保存草稿结构失败缺少可靠回退|新组合/恢复路径重新验证字典；坏候选丢弃，恢复程序基线；正文与composition共同绑定report_hash，复核后不得换内容，导出再次核对|
|P2 发送前拒绝误记UNKNOWN|普通预算RuntimeError经SDK包装后进入通用错误分支；固定回调也把所有错误记为未知|诊断v16和固定v5共用持久化调用边界。仅类型化守卫、同一调用身份、零admission及可靠journal证明齐备时记NOT_SENT/已知0；调用次数不退，不继续修正或AI复核|
|P2 恢复及无效用量|崩溃可丢失结算；SDK可能将不完整usage转换成可解析对象|拒绝证明先落盘，再幂等结算；新租约可恢复匹配证明，旧租约被拦截。证明缺失/改变不能复用已知0；真实超时、取消、无效usage保持UNKNOWN|
|P3 历史安全对账|原v15 UNKNOWN阻止标准审计与费用承接|实现独立验证器、只读评估、追加证书、显式审计/承接和递归验证；无证书仍严格拒绝。当前旧事件无直接身份，输出BLOCKED，不生成更正证书|
|集成预检补充|整数Literal版本配置无法读取环境字符串`"5"`，显式v5启动失败|仅转换字符串1—5，保留版本闭集；0/6/5.0/5x仍拒绝。代码默认v16/v5，旧配置和任务路由保留|

新版本为diagnosis-v16/Prompt-v16、报告schema4、answer-contract-v4、提议V3、工具注册表6；固定commerce-review-bundle-v5/Prompt-v5。accounting_protocol=2由服务端冻结，新题包语义侧表schema3须同时绑定运行版本与计量协议。

显式请求的解释方向优先于可选方向；未完整展示时必须保留incomplete。可选省略只列方向名称，不用limitations偷偷重新塞入整段被省略内容。九项金额、记账恒等式和完成说明都有独立删除反例。

## 2. 计量与权限不变量

- NOT_SENT必须有受控守卫产生的类型化异常及持久化证明，绑定Task、call、step、phase、attempt、原lease、prompt/schema摘要、模型与运行journal。异常文字、空request_id、没有trace都不是充分证据。
- 守卫admission之后发生的取消、超时、连接错误或不完整响应不能记零。结构失败但usage有效仍按FAILED收取真实用量；非流式提供方usage核对失败时，SDK补字段不能将其转成已知。
- 每次调用仍占用一次model_calls，只结算一次usage_reports。固定v5隔离旧RuntimeModelMeter回调，避免预占或扣费翻倍。非本轮旧研究流程保留原保守计量。
- 草稿、缓存、恢复和新模型调用继续重新授权；撤权、取消、旧租约及篡改输入均不能因“复用”绕过。证明落盘前崩溃仍保守UNKNOWN，不凭未来步骤补证。
- 固定v5和诊断v16预算停止均不再调用模型；保留可核对事实，标明未完成，不能显示请求已答齐。

## 3. 历史对账为何仍阻断

[历史核验回执](acceptance/diagnosis-v16-local-20261026/historical-reconciliation.json)给出：

`RECONCILIATION_BLOCKED / historical_denial_has_no_unique_direct_attempt_binding`

原v15 journal拒绝事件缺少call_id/task_id/lease/prompt_hash，无法满足直接绑定。唯一末尾UNKNOWN、唯一拒绝、总量对齐和当前只读HTTP审计能够解释现象，但没有补出本来未记录的身份/并发证明。本次没有采用仅凭顺序就放行的旧格式启发式。

因此原UNKNOWN、usage_complete=false、H03两份失败、H13漏答失败和21份未执行全部保留。没有写回原Task，也没有生成伪造零用量证书。新验证器对具备直接证明的合成反例能安全生成/核验证书，并已验证完整祖先费用、排他承接及不继承旧质量成绩；这些成功不能移植到当前v15历史运行。

可复现的只读命令：

```sh
PYTHONPATH=. .venv/bin/python -m evaluations.diagnosis_reconciliation evaluations/results/diagnosis-v15-h01-h20-live-20261026-01 --output /private/tmp/v15-reconciliation-assessment.json
```

验证通过的其他记录可显式加`--certificate-output`追加独立证书；被阻断时不生成证书。标准审计可用`evaluations.diagnosis audit --reconciliation`显式验证，费用承接可用`run --charge-from ... --prior-reconciliation ...`；无证书时保持原阻断，证书不授予调用许可。

## 4. 预算与正式效果边界

累计仍为10,954,662/12,000,000 token、1576次HTTP，剩余1,045,338；下一次保守预占1,051,576，缺6,238。没有修改上下文预占算法、提高额度、退款或自动重试。即使历史对账以后能通过，也不能在当前余额下启动下一次真实发送。

[50号正式结果](50-v15-formal-validation-budget-stop-20261026.md)仍是原始部分结果：75/96已执行，72 PASS/3 FAIL，21 UNTESTED。新版本没有真实模型成绩，不能把本地回放、旧正式成绩或剩余题目的拼接当作v16完整正式轮。

本轮P1—P4之外的新正式题包准备/冻结与真实发送没有启动。后续需要先处理历史证据与预算门禁，再准备全新题包并独立授权；未执行T14—T20没有被拿来进行本轮图/工具开发回放，本地回归使用已执行Q资料。

## 5. 独立验收清单

原始日志、源码指纹、冻结核对、保存响应重组、旧评分和缺陷注入见[证据入口](acceptance/diagnosis-v16-local-20261026/README.md)。以下区分“实现防线通过”与“真实历史可放行”。

|51号编号|验证内容与证据|判断|
|---|---|---|
|T01|保存H03三份4/4/3响应用生产compose重组，JSON/MD/PDF逐项对齐；原始回执不改|PASS_LOCAL（非新Task/正式成绩）|
|T02|1/2/3/4候选、满8说明、无模型/停止基线；空提议按Schema拒绝|PASS_LOCAL|
|T03|非法第4项、非法动作/范围、额外字段，必须完整拒绝；裁剪顺序缺陷注入|PASS_LOCAL|
|T04|标题、claim文本、章节数量、20条限制容量及字典重校验|PASS_LOCAL|
|T05|程序说明满8不删除；9条无效基线不得导出|PASS_LOCAL|
|T06|九项金额逐个删除、恒等式与完成约束、显式解释方向遗漏|PASS_LOCAL|
|T07|真实checkpoint保存坏草稿，恢复validate后回退程序基线并重新待复核|PASS_LOCAL|
|T08|双入口撤权/取消/旧租约恢复；实际取消传播、旧版本权限/CAS回归|PASS_LOCAL|
|T09|正文/组合元数据篡改导致复核拒绝；复核与导出hash绑定|PASS_LOCAL|
|T10|诊断/固定完整SDK钩子拒绝，transport0、用量0、次数保留1|PASS_LOCAL|
|T11|SDK因果链及并发ContextVar隔离；独立Task不混淆身份|PASS_LOCAL|
|T12|相同文字与伪造类型不能得到已知0|PASS_LOCAL|
|T13|transport超时/取消/缺usage/负数/总数缺失或不一致仍UNKNOWN|PASS_LOCAL|
|T14|成功、结构失败、重复结算、缓存复用与接管不重复计量|PASS_LOCAL|
|T15|证明落盘前后崩溃；前者UNKNOWN，后者新租约幂等恢复，旧租约拦截|PASS_LOCAL|
|T16|journal写失败/删除/改变与无数据库证明，不能确认已知0|PASS_LOCAL|
|T17|跨Task身份、伪造证明、范围/租约不符、证明重放/篡改被拒绝|PASS_LOCAL|
|T18|固定v5完整Worker两阶段调用，计数与用量不翻倍|PASS_LOCAL|
|T19|双入口预算停止交付，只有一次尝试，不继续模型复核/纠错|PASS_LOCAL|
|T20|v16成功响应在checkpoint空窗恢复；1/12调用预算及PG复验|PASS_LOCAL|
|T21|235份旧源码副本、199份历史核心文件及75份原始成绩保护|PASS_LOCAL|
|T22|实际v15旧格式直接绑定证明缺失；安全输出阻断|BLOCKED（防线行为通过）|
|T23|双UNKNOWN/双拒绝、错身份/运行、缺trace、证明缺失等对抗副本|PASS_LOCAL|
|T24|证书费用、身份、文件hash、源码及验证器摘要篡改|PASS_LOCAL|
|T25|不提供证书，原strict审计仍拒绝UNKNOWN|PASS_LOCAL|
|T26|完整祖先费用、证书递归承接、原排他锁、零质量继承|PASS_LOCAL；实际v15承接BLOCKED|
|T27|新默认幂等重试不迁移旧任务；修改源码不能拼接质量结果|PASS_LOCAL|
|T28|现有1200万额度与预占差额保持，拒绝发生在HTTP前|PASS_LOCAL；真实发送不可放行|
|T29|旧诊断v1—v15、固定v1—v4路由/恢复及新版本配置闭集|PASS_LOCAL|
|T30|v11/v14各96份及v15已有75份逐字段重评不变|PASS_LOCAL（267份）|
|T31|真实本机隔离PG、HTTP、备份/权限/取消/恢复与新增入口反例|PASS_LOCAL（1059项，0跳过）|
|T32|JSON/MD/PDF、Chrome实际API、刷新、移动端、九项金额和完成语义|PASS_LOCAL（19项Chrome、3组保存响应导出）|
|T33|schema3版本/协议缺项或错配，在凭据/HTTP前拒绝；旧侧表保留|PASS_LOCAL|
|T34|全套运行外部DNS/socket哨兵；未发生提供方网络调用|见network-guard-release.json|
|T35|禁用容量、先裁剪再验证、异常文字记零、所有错误记NOT_SENT、跳过UNKNOWN共5种缺陷|PASS_LOCAL（5/5检出）|

## 6. 验收过程中发现并处理的阻断

1. 旧预算异常文字被新实现改变：恢复旧协议错误字符串，保留历史契约，不靠改断言掩盖失败。
2. 固定v5未接入独立数值/完成评分入口：仅扩展新版本路由，旧版本逐字段回归保持。
3. v16预算停止说明未登记为程序可验证缺口：补齐新版本白名单，并核查停止后的报告仍为incomplete。
4. 显式v5环境字符串启动失败：增加受限转换及1—5/非法值反例。
5. 已完成NOT_SENT缓存恢复没有重验外部证明：补齐两条复用入口的证明校验、删除/篡改及重复结算反例。
6. 第一轮全量的HTTP keepalive测试结束时任务仍running；由Worker在StaleLease时提前返回的控制流，定位为租约栅栏路径。单独诊断未复现失败（模型初始化0.188秒），因此将独立租约与deadline条件隔离。该测试现在用30秒租约隔离此条件，仍保留2秒绝对deadline、1秒read timeout和5秒外层断言；全部14项超时测试通过，生产超时和租约策略未放宽。

7. 交付指纹Chrome首次完整轮18 PASS/1超时：同输入对照仍显示排队，已读trace中请求停止且存在约237秒时间间隙；随后原15秒断言/30秒测试超时下单独串行复验通过（13.1秒），完整串行复验19项通过（51.6秒），不修改产品或延长断言。此处只记录现象，未将具体宿主暂停原因当成已证实。Playwright在下一次启动时清理了原trace，保留失败日志及此前工具提取的观察，不能声称原zip仍在。

最终发布资格仍须由新正式轮判断。本地绿色只证明本轮实现、兼容和安全边界，不能关闭G6。
