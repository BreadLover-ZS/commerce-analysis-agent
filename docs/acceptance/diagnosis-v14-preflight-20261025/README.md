# v14真实模型前本地预检材料

范围：G0—G6关键代码、旧版本兼容、权限和恢复、独立反例、题包及预算保护；不调用真实模型，G7排除。完整结果与边界见[46号报告](../../46-pre-model-code-preflight-results-20261025.md)。

|文件|含义|
|---|---|
|receipt.json|最终源码、验证数量、原始日志hash、账本和未验证出口|
|implementation.json|最终225个实现/测试/配置文件的逐文件hash及整体指纹|
|probe_worker.py|21条真实Worker/工具与协议替身回放，包含27/20/7缺口、错误维度、范围汇总及等价路径|
|probe_scope.py|相同已执行开发数据下，旧v13漏答范围汇总和新v14阻止错误完成的两项对照|
|probe_recovery.py|v13/v14×调用上限1/12，保存响应后异步取消、重建Worker、复用账本；仅协议替身|
|probe_invariants.py|固定种子239份数据×2范围，独立整数算术及10条gateway边界控制|
|*-summary.json、postgres.log、chrome.log、history-audit-summary.json|可提交的紧凑结果；原始完整Task、工具证据和产物保留在忽略目录|
|SHA256SUMS|上述交付材料完整性|

完整原始过程位于 `evaluations/results/diagnosis-pre-model-preflight-20261025-01/`，保留初始失败、草稿源码、修复后回放及最终证据。历史正式数据未改写，新v13/v14正式题包没有进入图或模型。

在项目根目录复现，输出目录应使用新的名称，脚本拒绝覆盖：

```sh
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v14-preflight-20261025/probe_worker.py --output /tmp/commerce-v14-worker-replay
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v14-preflight-20261025/probe_scope.py --output /tmp/commerce-v14-scope-replay
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v14-preflight-20261025/probe_recovery.py --output /tmp/commerce-v14-recovery-replay
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v14-preflight-20261025/probe_invariants.py --output /tmp/commerce-v14-invariants-replay
.venv/bin/python scripts/test_embedded_postgres.py tests
```

这些回放仅使用已经执行的开发/回归数据。真实PG/HTTP和浏览器是本机集成证据，协议替身不能证明模型选择、真实请求效果或生产可靠性。
