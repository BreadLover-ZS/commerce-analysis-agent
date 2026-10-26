# v16本地修复证据（2026-10-26）

对应[52号修复与验收](../../52-v16-repair-and-local-validation-20261026.md)，实施范围为[51号方案](../../51-v16-repair-plan-and-confidence-gates-20261026.md)P1—P4。代码默认诊断v16/固定v5，计量发送协议v2；历史UNKNOWN没有改写，真实模型效果尚未验证。

|材料|证明内容|
|---|---|
|`receipt.json`|最终指纹、验证数量、历史保护与明确阻断项|
|`postgres-delivery.log` / `network-guard-delivery.json`|交付指纹完整隔离PostgreSQL套件，外部DNS/网络哨兵|
|`chrome-serial-final.log`|显式DIAGNOSIS_RUNTIME=v16、COMMERCE_REVIEW_ANSWER_VERSION=5、Chrome，本机真实demo API、刷新、移动端、PDF|
|`frontend-build.log`|TypeScript及Vite构建；本轮没有修改前端实现|
|`saved-response-replay-delivery/receipt.json`及3组JSON/MD/PDF|原H03的4/4/3响应使用生产compose重组，结构、指标及披露一致；不是新Task、正式轮或模型质量成绩|
|`legacy-regrade-delivery.json`|v11/v14各96份、v15已有75份原始评分逐字段不变|
|`mutation-results-delivery.json`|5种缺陷在独立源码副本中全部被反例检出；工作区源码不改|
|`historical-reconciliation.json`|实际v15安全核验BLOCKED，缺直接调用关联，不生成证书|
|`protected-history.json` / `preimplementation-frozen-check.json` / `frozen-evidence-delivery.json`|实施前后235份旧源码副本与199份历史核心文件的保护，以及最终244份源码副本|
|`development-pg-first-failure.log` / `development-timeout-diagnostic.log`|独立短租约与deadline测试条件干扰的失败/诊断记录；最终生产超时策略未放宽|
|`chrome-final.log`|显式整数Literal环境变量启动失败；受限字符串转换已修复|
|`chrome-delivery.log` / `chrome-comparison-isolated.log` / `chrome-comparison-clock-gap.json`|一次18/19轮与对照页原断言串行通过，及工具已读取的长时间trace间隙观察；原zip被Playwright下一轮清理，未声称仍保留|
|`SHA256SUMS`|本目录最终材料及52号报告完整性|

最终244文件源码副本与清单在忽略目录`evaluations/results/diagnosis-v16-local-20261026-01/delivery-source-snapshot/`和`delivery-source.json`；不含.env/密钥。旧v15源码副本及原始账本保留在原目录。若只复制Git文件，不包含这些忽略目录及原评测回执；完整复现须同时保留它们。

复现时从仓库根目录执行，输出路径须是尚不存在的新路径：

```sh
.venv/bin/python scripts/test_embedded_postgres.py -p tests.no_provider --disable-warnings
DIAGNOSIS_RUNTIME=v16 COMMERCE_REVIEW_ANSWER_VERSION=5 COMMERCE_BROWSER_CHANNEL=chrome pnpm --dir frontend test:e2e --reporter=line
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-local-20261026/regrade_history.py --output /private/tmp/v16-regrade-recheck.json
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-local-20261026/mutation_checks.py --output /private/tmp/v16-mutations-recheck.json
PYTHONPATH=. .venv/bin/python docs/acceptance/diagnosis-v16-local-20261026/replay_saved_reports.py --output /private/tmp/v16-report-recheck
```

三份重组PDF是离线报告内容一致性证据；JSON及全部原始来源是复现依据，不能拿这些PDF替代原任务正式产物。后端与浏览器完整验收建议串行运行，保留失败日志后再启动下一轮Playwright，避免其自动清空输出目录。

本轮新增真实提供方请求0，累计额度仍1200万token；当前历史承接仍阻断，且下一次保守预占仍差6238token。证书、准备材料或本地PASS均不授权真实调用，G6未放行，G7排除。
