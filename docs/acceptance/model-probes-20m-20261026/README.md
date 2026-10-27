# 2000 万累计预算下的小批探针证据

2026-10-26 开始，2026-10-27 收尾。完整结果见 [56 号报告](../../56-small-model-probe-results-20261027.md)，H01 首错根因与修复见 [55 号报告](../../55-H01-probe-budget-root-cause-and-repair-20261026.md)，原计划见 [54 号文档](../../54-model-probes-20m-plan-20261026.md)。

## 当前结果

- [results.json](results.json)：八次尝试、七类探针及每份回执、审查、源码、费用与核心文件 hash。
- [final-audit.log](final-audit.log)：只读全链审计，本审计不执行 Task 或发请求。
- [authorization.json](authorization.json)、[conservative-hold.json](conservative-hold.json)：用户授权和唯一旧 UNKNOWN 的保守占用证明。
- [final-historical-reconciliation.json](final-historical-reconciliation.json)：旧严格对账仍 BLOCKED，无证书；不是用保守冻结替代严格对账。
- `*-review.json`：逐份作者侧 AI 审查，绑定原回执 SHA256；H01 初测仍 FAIL。

本轮真实新增 11 HTTP／94,907 token，新增 UNKNOWN 0；累计已知 11,049,569，历史未知冻结 1,051,576，有效占用 12,101,145，20,000,000 内余 7,898,855。冻结不是实际消费。两次零模型程序执行不能计为模型效果，旧输入探针不能计为新保留集 G6 通过。

## 修复后的本地证据

- [readiness-H01-repair.json](readiness-H01-repair.json)、[H01-repair-evidence.json](H01-repair-evidence.json)：最终源码 `6a628584…`、254 文件、失败父回执与准入证据绑定。
- [H01-repair-postgres.log](H01-repair-postgres.log)、[H01-repair-network.json](H01-repair-network.json)：1337 PASS，隔离 PostgreSQL，提供商请求和被阻外部尝试均为 0。
- [H01-repair-chrome.log](H01-repair-chrome.log)：19 PASS，串行运行。
- [H01-stop-mutations.json](H01-stop-mutations.json)：13 类行为缺陷检出，另有一个单守卫冗余控制存活；每个基线退出码均为 0。
- [H01-offline-replay-release.json](H01-offline-replay-release.json)：原 H01 顺序仍 FAIL，两个工具的高效顺序严格 PASS；真实提供商请求 0，模拟用量不计入用户账本。
- [H01-legacy-regrade.json](H01-legacy-regrade.json)、[H01-frozen-check.json](H01-frozen-check.json)：267 份历史评分不变，199 份原件及 69 项旧证据、五套源码字节保护。
- [H07-coverage-contrast.json](H07-coverage-contrast.json)：实际已启用缺两行，与本地派生的启用前零适用记录分别验证，均零模型请求；正式输入原件未改。

## 首次准入和开发记录

初始 `readiness.json` 对应 `7ac0cfaa…`、252 文件、1316 后端、19 Chrome；保留当时证据，没有覆盖成修复后的结果。它绑定的原启动器已按原 hash 保存在 `launch_one_probe-original.py`，仅供历史核验；当前启动器是 `launch_one_probe.py`，使用修复后的独立 readiness。

`preflight-*`、`hold-tampering.json`、`stop-mutations.json`、`legacy-regrade-preflight.json` 是付费前的验证。`*-red.log`、`*-development.log`、`*-fixture-error.log`、早期失败或中断日志是开发记录，不作为最后通过证据。初始夹具修正、误启动中断和 H07 审查误读已在 54—56 号文档说明；没有隐藏成全绿历史。

## 复核方式

`collect_results.py` 只读审计当前链；为避免覆盖已交付记录，再执行前应在副本或改用新的输出位置。`prepare_repair_readiness.py`、回放与变异工具也使用排他写入，不可把文件已存在错误当成需要重发模型请求的理由。

原始完整材料在本机忽略目录 `evaluations/results/model-probe-*/`，路径和 hash 在 `results.json`。源码快照是准入时字节，不能清理；模拟回放与真实账本分目录。`SHA256SUMS` 校验本目录证据，`receipt.json` 记录交付状态。未提交、未推送。

所有真实执行仅由用户授权的一次 Task 启动；没有定时任务或后台自动扩跑。下一次请求必须承接当前叶子 H07、绑定当前源码与审查，不能从较早 PASS 分支重新起账。新 UNKNOWN 必须停止，不能自动冻结后重试。
