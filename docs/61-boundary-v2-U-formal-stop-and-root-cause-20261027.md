# U 真实模型验证首错停止与根因报告（2026-10-27）

**正式轮失败并停止。执行 5／96 Task 后，第 5 位 H01-3/fixed 发生模型报告回退，未再发送真实请求；91 位未执行。** 本轮不是完整正式轮通过，不放行 G6，G7 排除。按用户“逐 Task 审查、首错即停、不自动重试”授权执行，见 [60 号计划](60-boundary-v2-U-formal-model-test-plan-20261027.md)。

本轮新增 **10 HTTP／72,552 token**，新增 UNKNOWN 为 0。当前累计已知 **11,122,121 token／1597 HTTP**，旧 UNKNOWN 保守冻结 **1,051,576**，有效占用 **12,173,697**，2000 万上限内剩余 **7,826,303**。严格历史对账仍 BLOCKED，没有伪造证书、退还未知消费或增加额度。

## 成绩必须分层

| 执行位 | HTTP／token | 原自动判定 | 执行时内容审查 | 追加上下文准入审计 | 严格联合结论 |
| --- | ---: | --- | --- | --- | --- |
| H01-1/fixed | 2／11,764 | PASS | PASS | FAIL：语义复核证据为空 | FAIL |
| H01-1/agent | 2／18,913 | PASS | PASS | 不适用固定语义复核路径 | PASS |
| H01-2/fixed | 2／11,354 | PASS | PASS | FAIL：语义复核证据为空 | FAIL |
| H01-2/agent | 2／18,819 | PASS | PASS | 不适用固定语义复核路径 | PASS |
| H01-3/fixed | 2／11,702 | PASS | FAIL：模型报告回退 | FAIL：语义复核证据为空 | FAIL |

原自动 **5／5**、执行时作者侧内容审查 **4／5**；追加准入审计后的严格联合为 **2／5**。这不是整轮通过率或外部业务验收。三份固定报告的 27 项逐商品金额、贡献与汇总可由程序和原始行复核，最终报告仍可读；但模型复核输入无权威证据，不满足完整验证。不能因为兜底报告正确或自动评分绿色就把真实调用失败算通过。

前两份固定报告内容审查通过时，没有把已显示的空证据选择列为准入失败，这是本轮预检和审查漏项。原 `review.json`、成绩和失败均保留；另追加绑定 receipt hash 的 `post-audit.json` 更正正式结论，不覆盖历史 PASS 来隐藏过程。模型前两次认可空证据也不构成证据，不能据此选择性忽略第三次拒绝。

## Root Cause Analysis

**Error**：`model_report_fallback`。第 5 位模型语义复核返回 19 个无法支持的结论 ID，并明确指出 `selected_ids` 为空、唯一经营证据因 `character_budget` 被排除、实际证据列表是 `[]`。

**Expected**：复核收到与最终报告所有引用对应的真实授权证据，能独立检查事实与建议前提；容量不足时在发送前停止，不能付费请求模型对空证据做判断。

**Cause**：`app/research/context.py:evidence_package()` 对固定 v6 聚合了事实目录、重复 metrics、完成度等多份元数据。H01 单份证据的候选序列化长度为 **28,659 字符**，超过 28,000 硬上限 **659** 字符。现有策略整份排除该源，选中内容只剩两个字符 `[]`。`app/research/graph.py:validate` 仍把完整报告和空证据送给 `SemanticReview`，缺少“报告引用在上下文中全部可解析”的发送前门禁。

本轮三份固定 Task 的实际第 2 个 HTTP 请求均直接验证为空，不是从模型回复反推；随后只对已经执行的 H01 持久化证据做离线打包重现，候选长度都为 28,659。候选字段主要为 fact_catalog 17,240 字符、answer_completion 3,989、metrics 2,796；直接增加额度或换个模型不会修复证据丢失。前三次固定请求均受影响，第三次拒绝是合理行为。

**Fix**：详见 [62 号修复方案](62-fixed-review-evidence-admission-repair-plan-20261027.md)。需要版本化的紧凑权威证据投影、完整引用覆盖校验、容量不足的付费前停止，以及独立评分和实际 SDK 消息断言。不能仅提升字符上限、关闭语义复核、让模型无条件 PASS 或删除失败断言。本轮没有改业务／评测源码，也没有宣称修复完成。

**Prevention**：1420 项本地绿色测试未覆盖“新固定 v6／子策略 2 的真实大小证据＋实际语义复核请求”组合。已有最大商品上下文测试主要覆盖旧契约，部分本地语义 Mock 固定返回空拒绝列表，无法抓到证据为空却继续调用。后续必须核查线上实际发送体，不以提示中包含报告自身的数字代替独立证据。

## 停止与账本证据

[results.json](acceptance/diagnosis-boundary-v2-U-live-20261027/results.json) 保存全部五位原判定、追加审计、费用和 91 个未执行位。[root-cause-evidence.json](acceptance/diagnosis-boundary-v2-U-live-20261027/root-cause-evidence.json) 绑定实际 HTTP 请求与回执 hash、空证据及字段大小；[stop-gate.json](acceptance/diagnosis-boundary-v2-U-live-20261027/stop-gate.json) 验证下一位在凭据前被 `formal_failed_round_stop_repair_refreeze_required` 拒绝，输出目录和失败叶子的后继预算 claim 均未创建。

完整原始结果：`evaluations/results/diagnosis-boundary-v2-U-live-20261027-01/`；源码指纹始终为 `6e2308f2cfcbc3a36c0ef64000bff22ef7a75056e6f8766a4ff4adc952073ef4`，267 文件。330 份既有受保护原件未变；旧冻结材料保留执行前状态，实际进度以本轮账本为准。新授权单独保存，没有将原未授权请求改成授权。

离线分析网络请求为 0，仅使用已执行 H01 的记录，没有把未执行 U／旧 T14—T20 送进 Worker、业务工具或模型。H01 已使用，不能再称新盲测。两份诊断分别与对应固定流程使用同 bundle id／hash；各 Task HTTP 上限均为 2、SDK 无重试。源码未修改，材料尚未提交、推送。
