# v13修复复验材料

对应[42号方案](../../42-v13-request-obligation-repair-plan.md)和[43号结果](../../43-v13-request-obligation-repair-and-validation.md)。原v12验收失败保留在相邻目录，不覆盖原始证据。

|文件|用途|
|---|---|
|`probe_worker.py`|13条字面需求验收、预算缺口、正确/错误维度、合法等价路径、伪造完成反例；执行完整Worker和真实工具/导出|
|`probe_invariants.py`|注册表6的独立原始行算术、每SKU金额/件数类型、覆盖和网关边界|
|`worker-probe-summary.json`|13条验收的Task ID、断言、停止原因及完整缺口计数|
|`invariants.json`|239组数据、72,478条算术断言及10项网关检查摘要|
|`v12-counterfactual-summary.json`|同一旧脚本的8 Task反向对照，4个语义失败继续重现|
|`historical-grading-check.json`|原v11正式96份评分不变及祖先预算重新对账|
|`old-v12-plan-guard.json`|源码变化后拒绝沿用旧冻结计划的零请求检查|
|`receipt.json` / `SHA256SUMS`|最终源码指纹、命令/日志与原始回执hash；本目录校验和|

本机完整原始证据：`evaluations/results/diagnosis-v13-repair-20261023-01/`，Git忽略。包含各次失败与修复后运行日志、59个离线Task、13个修复回放Task、8个v12对照Task、证据/账本/5种导出及历史评分审计。不要将同一数据多次回放写成独立业务样本。

从仓库根目录运行，下列输出目录必须尚不存在：

```bash
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v13-repair-20261023/probe_worker.py \
  --output evaluations/results/v13-worker-reproduce-01
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v13-repair-20261023/probe_invariants.py \
  --output evaluations/results/v13-invariants-reproduce-01
.venv/bin/python scripts/test_embedded_postgres.py tests
```

前两条不加载模型密钥或发送提供方请求；使用已执行开发数据和固定随机seed，不执行v12未运行的正式题包。Worker脚本所有断言通过才退出0；“正确保留预算缺口”属于局部验收通过，不能称需求完整完成。第三条需要项目依赖、可选pgserver和本地服务端口。

新规则只用于v13；原版本行为和正式历史评分保留。预期来自字面要求和独立整数行运算，但作者仍是参与开发的AI助手，不是组织独立第三方或外部业务盲测。G6未放行，G7排除，付费新增0。
