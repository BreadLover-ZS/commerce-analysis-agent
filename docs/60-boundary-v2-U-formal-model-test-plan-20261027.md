# U 题包真实模型验证计划（2026-10-27）

用户本轮明确授权将冻结 U 题包的合成销售、库存、规则、问题及任务上下文发送至 DeepSeek（https://api.deepseek.com），使用项目现有密钥，执行 20 题／96 Task；保留并承接旧 UNKNOWN 的 1,051,576 token 保守冻结，累计上限仍为 2000 万。对应确认答复：“授权上述范围，逐 Task 推进（推荐）”。授权记录单独保存，不修改上一轮未授权请求或冻结材料。

输入为 [schema5 冻结包](acceptance/diagnosis-boundary-v2-formal-U-20261027/README.md)，源码指纹 `6e2308f2cfcbc3a36c0ef64000bff22ef7a75056e6f8766a4ff4adc952073ef4`。前置本地验收为 [59 号报告](59-boundary-and-formal-entry-local-validation-20261027.md)：1420 项后端、19 项 Chrome、9 类故障变异通过；本轮启动前重新核对冻结源码和证据散列，未重复执行新题作本地调试。

由 `evaluations.diagnosis_formal` 一次启动一位，从 H01-1/fixed 开始。只使用 DeepSeek / deepseek-flash，temperature 0、输出上限 3000、SDK 重试 0、发送协议 v2。Task HTTP 额度按冻结位设置，决策保留 0；每次发送前检查源码和累计费用。每位自动通过后，再逐份阅读报告、证据、原始行期望、模型／工具调用和限制，写入绑定 receipt hash 的作者侧 AI 审查回执，PASS 后才启动下一位。

首次出现协议错误、调用异常、提议／工具拒绝、报告／完成度／任意自动评分失败、人工审查失败、UNKNOWN 或预算不足，即停止所有后续付费请求。保留原始失败、费用、HTTP 追踪及持久化工作库；不自动重试、不改历史判定、不通过放宽断言继续。失败后先分析根因与本地修复，是否新批次复测按新源码与冻结门禁另行处理。

初始累计已知 11,049,569 token／1587 HTTP，旧 UNKNOWN 冻结 1,051,576，占用 12,101,145，剩余 7,898,855。实际消费逐位从账本核验，不用估算顶替。严格历史对账继续 BLOCKED，冻结不代表已知消费；若出现新 UNKNOWN，不再自动新增冻结承接。额度不以花满为目标。

固定与诊断配对使用同 bundle id／hash。质量成绩仅归属于冻结源码；费用始终承接。真实结果另行形成报告，未执行位明确保留 UNEXECUTED。G6 需要完整结果和独立的最终门禁评审，G7 排除。本轮不提交或推送。

公共记录目录：[diagnosis-boundary-v2-U-live-20261027](acceptance/diagnosis-boundary-v2-U-live-20261027/)。完整原始证据位于 `evaluations/results/diagnosis-boundary-v2-U-live-20261027-01/`；其中没有密钥副本，运行期密钥由项目配置加载且不打印。
