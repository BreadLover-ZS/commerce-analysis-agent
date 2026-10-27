# 边界语义与正式单 Task 入口修复验收（2026-10-27）

本报告对应 [58 号方案](58-boundary-and-formal-entry-repair-plan-20261027.md)，范围为用户确认的“全部修复并完成本地闭环”：版本化边界判别、正式入口的逐 Task 审查／首错停止／预算承接、旧版本兼容及本地综合验证，重新冻结未执行题包。不调用真实模型、不增加额度，G7 排除；本轮没有提交或推送。

**本轮约定范围本地验收 PASS。** 最终实现指纹 `6e2308f2cfcbc3a36c0ef64000bff22ef7a75056e6f8766a4ff4adc952073ef4`，267 个源码／配置文件；完整快照位于 `evaluations/results/boundary-formal-repair-local-20261027-02/`。本地回执：[local-verification.json](acceptance/boundary-formal-repair-20261027/local-verification.json)。真实模型新轮仍 UNTESTED，正式发送授权待明确；原 UNKNOWN 严格对账继续 BLOCKED，G6 未放行。

| 结论 | 检查 | 证据 |
| --- | --- | --- |
| PASS | 完整本地后端及隔离 PostgreSQL 16.2：1420 项，221.94 秒 | [完整日志](acceptance/boundary-formal-repair-20261027/postgres-full.log) |
| PASS | 新策略 2 的 Chrome 串行端到端：19 项，55.5 秒；demo 模式 | [页面日志](acceptance/boundary-formal-repair-20261027/chrome.log)、[截图存档说明](acceptance/boundary-formal-repair-20261027/browser-evidence.json) |
| PASS | 前端构建、Prettier、Ruff 检查与格式 | [构建](acceptance/boundary-formal-repair-20261027/frontend-build.log)、[前端格式](acceptance/boundary-formal-repair-20261027/frontend-format.log)、[Ruff](acceptance/boundary-formal-repair-20261027/ruff-final.log) |
| PASS | 9 种行为缺陷变异，基线通过且变异后行为断言失败 | [变异记录](acceptance/boundary-formal-repair-20261027/mutation-results.json) |
| PASS | 267 份历史评分不变，330 份历史文件字节不变 | [重新评分](acceptance/boundary-formal-repair-20261027/legacy-regrade-final.json)、[历史保护](acceptance/boundary-formal-repair-20261027/history-protection.json) |
| PASS | 完整后端外部网络尝试 0，提供方请求 0 | [网络记录](acceptance/boundary-formal-repair-20261027/network-full.json) |
| PASS／未执行 | 新 schema5 包的作者原始行、执行位及冻结约束 | [冻结计划](acceptance/diagnosis-boundary-v2-formal-U-20261027/manifest.json)；96 位 UNEXECUTED |
| PASS | 用实际新冻结包检查未授权启动，在密钥加载前拒绝 | [入口拒绝回执](acceptance/boundary-formal-repair-20261027/frozen-entry-denial.json)，凭据加载／socket／新题执行均 0 |
| BLOCKED | 新包实际发送／正式保守承接授权；旧 UNKNOWN 严格对账 | 未发真实请求、未伪造证书或真实 claim |
| UNTESTED | 新题模型效果、外部业务验收及正式 G6 | 本地绿色测试不升级为正式通过 |

## 已修复的根因

57 号报告的“不适用的边界”同时被生产 v4 与独立评分 v3 漏识别。两个检测器一致不构成正确性证据。本轮先以作者明确标注的正负自然语句复现，再加入新 answer-contract-v5 和独立评分 v4；同时保留只在未被否定的子句之间传递语境，避免“无需说明边界，请解释退款原因”被错误判成边界问题。金额请求仍单独列为必答项，不能用边界说明替代本期金额。

新增服务端冻结字段 `answer_boundary_version=2`，新应用默认启用。没有该字段的旧 Task 按 v1／旧 v4 解释，部署配置改变与幂等重试都不能重解释原 Task。图版本仍为诊断 v16／固定 v6，计量协议仍 v2；schema5 正式题包同时绑定新的子契约，而非把新逻辑偷偷塞进旧冻结契约。旧完成度和规划模块保留，新策略使用独立版本模块。

固定流程在“比较期早于启用日、适用行数为零”时，原完成提示可能误称源数据不完整。新策略改成“边界与限制已答齐，仍不可计算的项目保持不可用”，独立判定同时检查适用行数与真实缺行数。启用前不适用仍不能补造比较期零值；启用后缺行仍保留真实证据缺口。

旧正式循环只对关键失败停止，也不能接续探针父账本。新增 `evaluations.diagnosis_formal` 单 Task 入口，复用抽出的 `diagnosis_one.execute_one`、现有 Worker、SDK 与 RequestBudget；旧探针的“只允许已使用旧输入”保护继续保留。新 schema5 包不能经旧批量入口误启动。

## 正式入口与节约 token 的约束

读取凭据前检查：完整材料和源码指纹、作者原始行义务、下一执行位、绑定授权、最新父链费用和排他承接、上一份回执对应的 PASS 审查、预算冻结及下一次最坏请求预占。输出目录重用、嵌入父结果或题包目录也提前拒绝。计划不能给自身授权；新包传输和正式保守承接分别明确授权，不能从旧探针授权推导。

每次仅一 Task；Task 模型额度等于该位 HTTP 上限，决策保留为零，SDK 重试为零，发送前重新核对源码。协议、调用、提议／工具、报告校验、任意自动评分、UNKNOWN、人工审查或预算失败均停止。有效的确定性兜底报告不掩盖模型失败；没有新一份绑定 receipt hash 的 PASS 审查就不能前进。失败后修复应重新冻结计划，不能把不同源码的质量成绩拼成一轮。

执行保留原始 HTTP 请求／响应追踪、源代码快照、费用 journal、业务输入、回执与持久化工作库。中断后不自动重发，已有响应不为补 checkpoint 再收费；新增 UNKNOWN 继续保留预占并阻断。固定与诊断配对从父工作库只读备份，复用同一个 bundle id／hash；最初新增配对反例确实抓到各自建库导致的身份不一致，修复后验证父库字节保持不变。

祖先链上限由 64 调为 256；冻结时检查整个 96 位可容纳。96 位链测试只构造元数据，费用与评分在该测试中明确模拟；真实 SDK MockTransport 正向验证的是相邻两 Task 的审查、四次模拟 HTTP、16 个新增模拟 token 和精确累计。二者均不是正式 96 Task 执行成绩。

## 本地证据与历史保护

证据目录：[boundary-formal-repair-20261027](acceptance/boundary-formal-repair-20261027/)。开发红例和修复前结果保留，不覆盖成 PASS：最初词义反例失败、否定语境泄漏失败、配对身份失败均有原始日志。部分开发失败属于测试预期或 Mock 协议构造错误，另行说明，不当成业务缺陷数量。

故意注入九类缺陷，在临时源码副本运行已有反例，全部被抓住：生产同义漏识别、独立判定漏识别、忽略冻结版本、接受 FAIL 审查、漏扣旧冻结、重建配对输入、忽略普通自动失败、无效业务草稿后继续复核、漏建零请求费用 journal。第一次用解析失败验证草稿护栏时，另一层持久化调用失败护栏仍会阻断，单护栏变异存活；保留初始结果，再以业务范围拒绝隔离验证该护栏，未伪称所有单护栏删除都会增加请求。

历史 v11／v14／v15 共 267 份原始回执逐份重新评分，判定字典全部不变；330 份受保护文件字节校验无变化，包括原 57 号冻结包、探针材料、原 v15 账本。最新真实父叶子仍未生成预算承接 claim。源码快照与当前实现绑定，日志中的 Mock 请求均由真实 SDK 走 MockTransport，本地网络插件禁止外部 DNS／连接。

## 重新冻结与未验证边界

新目录：[diagnosis-boundary-v2-formal-U-20261027](acceptance/diagnosis-boundary-v2-formal-U-20261027/README.md)。沿用从未执行的 U 原始数据，恢复 H07 自然表达“不适用的边界”，按 schema5／策略 2／v5 子契约重新冻结。旧目录 37 份冻结材料不改，新目录并非额外独立盲测家族。20 题／96 位仍全部 UNEXECUTED；95 项金额／件数、12 项每日指标、11 个贡献组仅通过元数据及原始行预检，未送入 Worker、图、工具或模型。

累计已知消费 **11,049,569 token／1587 HTTP**，唯一历史 UNKNOWN 冻结 **1,051,576**，合计占用 **12,101,145**，2000 万内剩余 **7,898,855**。本轮真实新增请求和 token 均为 **0**。旧 UNKNOWN 严格对账仍为 `RECONCILIATION_BLOCKED / historical_denial_has_no_unique_direct_attempt_binding`，不伪造证书，不把冻结当实际消费或已对账。

本地测试不能证明 DeepSeek 对新题包的实际答题质量、真实用量、外部业务验收或 G6 放行。未来须先确认新题传输与正式保守承接授权，再一位一位测试、审查；首错立即停下研究和修复，额度不以花满为目标。
