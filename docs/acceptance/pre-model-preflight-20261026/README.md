# 真实模型前的本地预检证据

对应[53号报告](../../53-pre-model-preflight-and-lessons-20261026.md)。这是本地代码、契约、兼容与故障边界证据；不替代真实模型质量验收。当前历史UNKNOWN和预算仍阻断，未增额、未发送提供方请求，G7排除。

|材料|用途|
|---|---|
|`receipt.json`|交付指纹、验证结果、准入状态与限制|
|`initial-independent-counterexamples.log`|修复前独立5条RED；不覆盖失败历史|
|`postgres-full.log` / `network-guard.json`|最终248文件指纹下1285项完整后端验证，包含真实隔离PG16.2；外部DNS/socket哨兵|
|`frontend-build.log` / `chrome.log`|TypeScript/Vite与显式v16/v6串行Chrome、本机demo API、刷新、移动端、PDF|
|`legacy-regrade.json`|v11/v14各96份及v15已有75份原始成绩逐字段一致|
|`historical-reconciliation.json`|实际v15仍BLOCKED，未生成更正证书|
|`actual-history-question-provenance.json`|实际15份祖先题源、207组业务输入的只读追溯；不触发Task/冻结/HTTP|
|`frozen-check.json` / `final-frozen-check.json` / `verify_frozen.py`|当前248文件与旧235/244文件字节副本、199份v15原件、前轮34项校验和保护|
|`mutation-results.json` / `mutation_checks.py`|9种真实缺陷在临时副本中应被反例检出，不改工作源码|
|`used-q-summary.json` / `replay_used_q.py`|旧已执行Q输入上的32次本地Worker回放；不是正式效果或未执行T题包开发|
|`development/`|开发阶段日志，含解析器初稿、题源合成夹具及运行环境失败；最终轮独立保留|
|`SHA256SUMS`|本目录与53号报告最终材料hash（不自包含）|

实现字节及完整Q回放在忽略目录`evaluations/results/pre-model-preflight-20261026-01/`，不含.env/密钥。复现还需要保留历史正式运行和祖先题源，不能仅复制Git文档。新正式schema4在合成元数据上验证过完整冻结流程；当前实际历史没有被冻结成可执行计划，也没有执行新题包。

在仓库根目录执行；JSON及回放输出须使用尚不存在的新路径。完整PG、Chrome、缺陷注入和回放串行运行，避免条件互相干扰。浏览器若失败，先将`frontend/test-results/`完整归档再启动下一轮，保留原trace，不调整超时来掩盖失败。

```sh
NO_PROVIDER_AUDIT=/private/tmp/precheck-network-new.json .venv/bin/python scripts/test_embedded_postgres.py -p tests.no_provider --disable-warnings
pnpm --dir frontend build
DIAGNOSIS_RUNTIME=v16 COMMERCE_REVIEW_ANSWER_VERSION=6 COMMERCE_BROWSER_CHANNEL=chrome pnpm --dir frontend test:e2e --reporter=line
PYTHONPATH=. .venv/bin/python docs/acceptance/pre-model-preflight-20261026/mutation_checks.py --output /private/tmp/precheck-mutations-new.json
PYTHONPATH=. .venv/bin/python docs/acceptance/pre-model-preflight-20261026/replay_used_q.py --output /private/tmp/precheck-used-q-new
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-local-20261026/regrade_history.py --output /private/tmp/precheck-regrade-new.json
PYTHONPATH=. .venv/bin/python docs/acceptance/pre-model-preflight-20261026/verify_frozen.py --output /private/tmp/precheck-frozen-new.json
PYTHONPATH=. .venv/bin/python -m evaluations.diagnosis_reconciliation evaluations/results/diagnosis-v15-h01-h20-live-20261026-01 --output /private/tmp/precheck-history-new.json
```

`tests/test_pre_model_plan.py`的合成账本只用于准入测试：通用99条假账行在生成该夹具时规范为96条，再取不可变快照。这不是对实际历史账本的修改，实际199份核心原件由独立hash核验。未执行T题包仅用于已有元数据/版本检查，没有进入Agent、Worker或工具回放。
