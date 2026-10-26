# v16修复方案证据（2026-10-26）

对应[51号方案](../../51-v16-repair-plan-and-confidence-gates-20261026.md)。本目录只证明方案的根因及局部可行性；业务修复、历史更正证书和v16本地综合验收均未完成。新增真实模型调用0，Task执行0，额度不变。

|文件|用途与边界|
|---|---|
|`probe_sdk_boundary.py` / `sdk-boundary-feasibility.json`|MockTransport及Pydantic边界，10项通过；不证明生产计量/崩溃恢复已修复|
|`probe_report_capacity.py` / `report-capacity-feasibility.json`|读取H03保存响应，5项通过；不修改原Task/分数，尚无省略披露和持久化组合实现|
|`verify_frozen_evidence.py` / `receipt.json`|核对235份实现文件、源码副本、199份历史核心文件及两份原型结果|
|`SHA256SUMS`|本目录最终材料与51号方案的完整性校验|

源码副本及历史hash清单在忽略目录`evaluations/results/diagnosis-v16-plan-feasibility-20261026-01/`，为本机实施基线，不含.env/API密钥。199份核心文件包括75份Task回执、39组三份输入/预期以及7份运行文件；不是整个历史目录的完整备份。原始正式审查与账本仍以[50号材料](../diagnosis-v15-formal-20261026/README.md)为准，标准审计及承接仍阻断。

从仓库根目录复现，使用新的输出路径，脚本拒绝覆盖已有结果：

```sh
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-repair-plan-20261026/probe_sdk_boundary.py --output /private/tmp/v16-plan-sdk-recheck.json
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-repair-plan-20261026/probe_report_capacity.py --output /private/tmp/v16-plan-capacity-recheck.json
.venv/bin/python docs/acceptance/diagnosis-v16-repair-plan-20261026/verify_frozen_evidence.py --output /private/tmp/v16-plan-frozen-recheck.json
cd docs/acceptance/diagnosis-v16-repair-plan-20261026
shasum -a 256 -c SHA256SUMS
```

两个原型禁止socket连接，SDK使用假密钥与MockTransport。实施修改源码后，冻结校验应指出当前源码已变化；不得覆盖旧清单把变化抹掉。历史UNKNOWN的证据唯一绑定尚未实现/验收，不能用本回执解除预算承接阻断。
