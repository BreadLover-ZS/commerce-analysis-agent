# v15 修复本地证据

完整解释见[49号验收](../../49-v15-repair-and-local-validation-20261025.md)，方案见[48号文档](../../48-v15-repair-plan-and-confidence-gates-20261025.md)。这些是本地功能、协议、真实本机PG/HTTP及浏览器证据；真实模型v15效果未验证，G6未放行，G7排除。

|材料|用途|
|---|---|
|receipt.json、implementation.json|最终状态、235文件指纹、验证数量及日志hash|
|postgres.log、focused.log、chrome.log、frontend-build.log|925完整后端、51专项（已包含）、19 Chrome及构建|
|offline-summary.json、used-q-replay-summary.json|59次开发/回归执行及32次旧Q输入本地回放|
|legacy-regrade.json|v11/v14各96份评分逐字段不变|
|replay.py|只读取已经执行过的Q题包，走真实Worker/工具/报告和独立评分；禁止socket.connect，不读取或执行新T题包|
|SHA256SUMS|本目录完整性|

完整原始数据及初轮失败保留在`evaluations/results/diagnosis-v15-repair-20261025-01/`及`diagnosis-v15-used-q-local-20261025-03/`等忽略目录。测试生成的5张文档截图已移入原始证据目录，工作区文档图片恢复原样。

在项目根目录复现，输出目录须为新路径：

```sh
.venv/bin/python scripts/test_embedded_postgres.py tests
.venv/bin/pytest -q tests/test_diagnosis_v15.py tests/test_diagnosis_v15_plan.py
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v15-local-20261025/replay.py --output /tmp/commerce-v15-used-q-new
(cd frontend && pnpm build && COMMERCE_BROWSER_CHANNEL=chrome pnpm test:e2e)
```

新T题包只能执行其[元数据预检](../diagnosis-v15-h01-h20/README.md)，不得拿它跑本地Agent回放。付费测试需要另行明确授权。
