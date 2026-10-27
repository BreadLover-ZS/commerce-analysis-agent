# U 真实模型轮：首错停止记录

完整结论见 [61 号报告](../../61-boundary-v2-U-formal-stop-and-root-cause-20261027.md)，后续修复见 [62 号方案](../../62-fixed-review-evidence-admission-repair-plan-20261027.md)。源码未改，缺陷未修复；不得继续旧计划的付费请求。

- 计划 96 位，执行 5 位，剩余 91 位未执行。第 5 位 `H01-3/fixed` 触发 `model_report_fallback` 后停止。
- 原自动 5 PASS，原内容审查 4 PASS／1 FAIL；追加上下文准入审计后严格联合仅 2 PASS／3 FAIL。原件不覆盖，更正审计单独保存。
- 新增 10 HTTP／72,552 token，无新 UNKNOWN。累计已知 11,122,121，旧冻结 1,051,576，占用 12,173,697；余额 7,826,303，上限 2000 万不变。
- 实际三份固定复核请求证据均为空：打包后 28,659 字符超过 28,000 后整源排除；第三次模型正确拒绝，前两次乐观 PASS 不能证明复核有效。
- `results.json` 为分层结果及未执行位；`root-cause-evidence.json` 绑定实际请求、源证据及长度；`stop-gate.json` 为凭据前阻断；`*-review.json` 为初始审查；`*-post-audit.json` 为追加更正。
- `authorization.json` 是本轮单独得到的明确用户授权，不修改旧冻结包中的未授权请求。`legacy-reconciliation.json` 保留严格历史对账 BLOCKED。

原始业务输入、bundle、HTTP 请求响应、账本、Task 回执、PDF／Markdown、源码快照与持久化库位于 `evaluations/results/diagnosis-boundary-v2-U-live-20261027-01/`，被 Git 忽略但为完整复现所必需。公共材料不含密钥。H01 已使用，不能再当新保留集；其他 U／T14—T20 未执行作开发调试。

分析脚本只读取已执行 H01，并在禁网络条件下重现打包问题；记录审查的脚本不会自动决定 PASS，也不会启动模型。每一真实请求均通过现有正式单 Task 入口，先审查上一份才启动下一份。G6 未放行，G7 排除，未提交／推送。
