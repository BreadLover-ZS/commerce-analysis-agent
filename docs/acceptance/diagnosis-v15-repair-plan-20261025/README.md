# v15修复方案的假设核验（未实施修复）

详细方案见[48号文档](../../48-v15-repair-plan-and-confidence-gates-20261025.md)。用户已确认保留evidence_gap并单独判断边界答复完整性，统一诊断/固定流程必答项。

**这里是方案证据，不是新正式题包，也不是v15修复通过回执。** 当前业务源码仍是v14，未新增提供方请求。

|文件|证据范围|
|---|---|
|probe_assumptions.py、assumptions-summary.json|26项根因检查；4个使用已执行H02/H17数据的真实Worker/工具与协议替身回放，原错序列仍失败、干净模型输出对照能计算|
|probe_schema_shape.py、schema-prototype-summary.json|13项最小Pydantic形状/拒绝/错误不回显/Schema稳定性检查；不是完整业务Schema或真实SDK质量验证|
|receipt.json|固定源码、原始证据与预算账本未变化、检查数量和明确未验证项|
|SHA256SUMS|本目录完整性|

H03实验数值见证仅支持本轮已知的单日回执，检查9项金额及整数恒等式并拒绝5种篡改；不应复制到生产当作通用多期/权限验证器。Schema原型里的tool_name=str、arguments=dict也只验证形状，生产仍须保留注册表/参数/所有权授权。

复现根因检查时使用新的输出目录（禁止覆盖），仅已执行数据，禁止socket.connect：

```sh
.venv/bin/python docs/acceptance/diagnosis-v15-repair-plan-20261025/probe_assumptions.py --output /tmp/commerce-v15-plan-root-probes
.venv/bin/python docs/acceptance/diagnosis-v15-repair-plan-20261025/probe_schema_shape.py --output /tmp/commerce-v15-schema-shape.json
```

完整4个Worker回执及日志在evaluations/results/diagnosis-v15-plan-assumptions-20261025-01/，Git忽略。两个脚本均不实现新业务逻辑、不修改历史回执，不加载真实模型或凭据。干净提议对照不是生产自动丢弃非法字段的建议。
