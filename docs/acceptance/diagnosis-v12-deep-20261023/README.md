# v12深度验收材料（2026-10-23）

结论与根因见[41号报告](../../41-v12-deep-local-acceptance.md)。基准提交4af0181，实现指纹e9bcf8aa1b7ebeb6b53903b33a15980c7908ac87e1ede658004711d10abf353c。
本目录只包含验收材料，不改变应用行为或冻结题包；所有新增Worker回放使用已执行的开发数据，不能当成新正式保留集。

|文件|用途|
|---|---|
|`receipt.json`|本轮结论、代码/预算基准及证据汇总|
|`worker-probe-summary.json`|主验收8 Task的字面预期与程序判断、Task ID、原始回执hash；4个虚假完整完成反例|
|`probe_worker.py`|实际Worker、隔离SQLite、SDK协议替身、真实工具和完整导出复现|
|`probe_invariants.py`|seed20261023的独立原始行算术与10项直接网关边界复现|
|`invariants.json`|239组数据、478次范围核对、20,374条算术断言及网关检查|
|`gap-truncation.json`|27项未完成只显示20项的独立产物核对|
|`pdf-integrity.json`|8份PDF的hash、页数、标题/文本可解析检查；不等于逐页视觉验收|
|`ci-runs.json` / `ci-jobs.json`|当前提交的只读CI结果；未触发重跑或部署|
|`SHA256SUMS`|本目录材料hash，校验和文件本身除外|

完整原始证据保留于本机 `evaluations/results/diagnosis-deep-acceptance-20261023-01/`：
包含755项PG/HTTP日志、17项Chrome日志、构建、8 Task的输入/报告/账本/5种导出、品类题自动评分对照、原v11审计、新计划预检、原生PDF视觉抽查及截图。
该目录由Git忽略；读者可用以下脚本在自己的隔离环境复现行为，新的UUID/文件时间戳和hash会不同。

从仓库根目录运行；输出目录必须尚不存在，程序拒绝覆盖。`.venv`沿用项目锁定依赖：

```bash
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v12-deep-20261023/probe_worker.py \
  --output evaluations/results/deep-worker-reproduce-01

PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v12-deep-20261023/probe_invariants.py \
  --output evaluations/results/deep-invariants-reproduce-01
```

预期：本轮还实际执行了复现脚本的另8个Task，结果与主验收完全一致，不作为额外独立样本。第一条正常执行并写出8份Task、完整产物与独立判断，4个语义反例为false；脚本退出0表示复现完成，**不表示项目通过**。
第二条产生239组有效随机数据、478次范围核对及20,374条算术断言；另有10项网关边界检查和1条汇总断言通过。两条都不读取`.env`模型密钥、不调用真实供应商、不执行新v12正式题包。

作者是参与项目开发的AI助手；独立指预期来自字面要求/单独的整数原始行核对，不能称组织独立第三方或人工业务盲测。
原v11正式评分不覆盖，本轮不宣称真实模型稳定通过率，G6仍未放行，G7排除。
