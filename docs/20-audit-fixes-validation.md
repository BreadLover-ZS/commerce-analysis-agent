# 审计修复与验证记录

日期：2026-10-16。基线：`b63970b4efb84f79e2d120d1c87d375eb0a12e41`，结果对应其上的本轮工作区修改。
用户授权实施[审计报告](19-code-audit-and-refactoring.md)中的 R1～R5，并同步项目文档；回归期间另修复了浏览器暴露的证据读取竞态（R6）。本记录不代表已提交、已推送或已部署。

## 1. 结果概览

| 项目 | 结果 | 适用范围 |
| --- | --- | --- |
| R1 恢复时指标变化 | 已修复，回归通过 | 首份指标快照、历史版本权威视图、回退失败终止；含真实 SIGKILL 和临时 PG |
| R2 库存临界分类 | 已修复，回归通过 | SQL 排序与 Python 输出使用相同未舍入比较；SQLite 与临时 PG |
| R3 RAGFlow 跳过附件 | 已修复，回归通过 | 本地真实附件与远端协议替身组合；不是实际 RAGFlow 验收 |
| R4 文档配额失效 | 已修复，回归通过 | 结构化身份及历史 URI 兼容；MD/PDF/DOCX/XLSX 定位形状 |
| R5 代理登录限流 | 配置与本地回归完成；容器链路 BLOCKED | 已安装 Uvicorn middleware、真实登录函数；本机无 Docker |
| R6 浏览器引用偶发无响应 | 已修复，浏览器回归通过 | 报告后再读取证据，避免新报告配旧账本 |

本轮没有数据库迁移或依赖升级。报告服务拆分、Repository 大拆分、前端状态重组仍是后续建议，没有为了修复缺陷扩大实施。

## 2. 每项修复的行为与边界

### R1：同任务的指标采用首次落库快照

调用链：`ToolGateway.metrics → Repository.metric_snapshot → 业务计算（未命中时）→ add_evidence`。
指标按任务、工具与分析周期固定首次成功登记的证据；恢复后即使业务表变化，也复用原证据 ID、数值和限制。
命中快照仍计一次工具调用，防止绕过工具预算。登记时在已有任务行锁与租约校验内再次查首份快照，解决并发首次写入。

新增 [storage/evidence.py](../app/storage/evidence.py) 统一指标范围和权威视图。
历史账本中的多版指标完整保留，上下文、指标渲染、报告校验和确定性模板只采用最早版本；
manifest 将其他版本记为 `superseded_metrics`，引用它们不会通过报告校验。
确定性回退若仍不通过，保留诊断事件，以 `report_validation_failed` 结束，避免无效循环到 GraphRecursionError。

固定的是已登记指标，不是全库快照：没有落库证据的空读取可重放，销售与库存仍有各自只读事务。
同一任务需要最新数据时应新建任务。本轮没有改写旧任务、删除历史证据或承诺 exactly-once 外部请求。

### R2：分类先比较原始比值，展示最后舍入

[inventory_risk_comparisons](../app/commerce/service.py) 用整数交叉相乘，统一 SQL 排序与 Python 分类：
`库存 × 周期天数 < 净销量 × 提前期` 判缺货，严格大于 `净销量 × 60` 判积压。
缺数、非正净销量分支继续先处理；相等不进入对应风险。

例如覆盖实际为 0.999 天而提前期为 1 天，展示可为 `1.00`，但分类仍为缺货。
会附上“展示两位小数，风险按未舍入比值判定”的限制，避免读者用显示值误判。
该估计仍不证明未来需求或实际供应商交期。

### R3：组织知识提供方与会话附件组合

local 模式保留授权组织规则与附件检索；RAGFlow 模式使用“本地当前用户/会话附件（最多 5 条）＋远端组织片段（最多 5 条）”，不混入本地组织样例。
本地查询始终先按租户、用户与会话过滤，不因远端配置放宽附件范围。

远端缺配置、空结果或可记录的 HTTP/响应格式失败时保留附件，并明确记录组织依据缺口。
远端越权数据集、缺失文档/片段身份仍拒绝，不能作为正常空结果隐藏。
证据保存 provider、dataset_id（远端）、document_id、chunk_id，显示真实检索提供方。

验证使用本地数据库和远端协议替身，没有访问实际 RAGFlow，也没有证明真实语料召回率或远端部署兼容性。

### R4：按结构化身份分配文档配额

本地文档按 `(local, document_id)`，远端按 `(ragflow, dataset_id, document_id)` 分组，每份文档最多选两条。
同一文档不同页、段落、工作表位置不再被当作不同文档；不同远端数据集中的同名 ID 不混为一份。
来源真正入包后才占用配额，超字符预算的候选不会挤掉该文档其他可用片段。

旧证据对已知 `document://` 和 `ragflow://` URI 推断身份，manifest 明示 `inferred_identity_ids`。
无法识别的历史身份记入 `unknown_document_identity_ids`，仅能保证总来源/字符上限，不能追认完整文档配额保证。
`exclusion_reasons` 分别记录旧指标、文档配额、总来源与字符预算排除原因。

### R5：明确每一跳的可信代理

Compose 固定前端与入口内部地址；API 只信任前端地址，Nginx 只信任入口地址。
Nginx 根据实际连接与可信入口识别客户端，并覆盖写入 API 的转发地址；直接客户端伪造 X-Forwarded-For 无法改变限流身份。
前端镜像通过过滤后的模板替换入口地址，保留 Nginx 的运行变量。生产入口仍负责 TLS。

新增 [test_proxy_headers.py](../tests/test_proxy_headers.py) 检查可信客户端分桶、未受信头拒绝及跨 IP 账户限流。
[proxy_smoke.py](../scripts/proxy_smoke.py) 和容器 CI 增加实际代理探针入口，目标限制为隔离本地 demo，拒绝非 demo 后才可能发登录探针。
探针会消耗失败限流额度，只能用于可丢弃环境；可信探针占用入口固定 IP，不得与生产入口同时运行。

本机没有 Docker，未运行实际镜像模板生成、Nginx → API 或 Caddy → Nginx → API 链路。
新增 CI 入口不是 CI 成功证据。地址配置、旧网络升级与运行条件见[部署说明](05-deployment.md)。

### R6：报告与证据按依赖顺序读取

Chrome 主流程首次出现“报告已展示，点击依据没有抽屉”。并行轮询可使证据先读到空账本，详情后读到已提交报告；点击处理只查本地列表，因此没有响应。

前端现在先取得详情/事件，再读证据，最后一起发布状态。因为报告只引用已经提交、持续保留的账本证据，后读证据可以覆盖该报告的引用。
[diagnostics.spec.ts](../frontend/e2e/diagnostics.spec.ts) 用延迟详情和旧账本响应稳定复现该窗口，修复前失败，修复后通过。
这是接口时序修复，没有给业务报告添加假数据或放宽断言。

## 3. 实际执行记录

| 验证 | 最终结果 | 说明 |
| --- | --- | --- |
| 新反例修复前 | 14 FAIL，3 PASS | 首批审计回归暴露原问题，之后继续增加故障与代理用例 |
| 后端完整回归 | **214/214 PASS，48.77 秒** | 单条命令；临时 PostgreSQL 16.2，含 16 条需要 PG 的用例，无跳过 |
| 故障恢复覆盖 | PASS，包含在 214 项中 | 销售/库存分别在证据提交处真实 SIGKILL，修改业务数据后接管；另有 PG 并发首写与临界分类 |
| 离线评估 | **32/32 PASS** | demo/local，模型调用为 0，不能解释为真实模型成功率 |
| 前端构建与格式 | PASS | TypeScript/Vite 构建与 Prettier |
| Chrome 浏览器 | **9/9 PASS，17.3 秒** | 一个真实本地 demo 主流程与八个接口替身诊断/时序用例；包含 PDF 文件头校验与移动端断言 |
| Python 静态检查 | PASS（应用范围） | 按 CI 检查 app/tests/migrations/evaluations/scripts，78 个 Python 文件格式通过 |
| 实际 Docker/公网 HTTPS | BLOCKED / UNTESTED | 本机没有 Docker；未连接目标服务器 |
| 实际 RAGFlow/Tavily/新版本真实模型 | UNTESTED | 未发真实外部服务请求；历史模型单例不能替代本轮验证 |

浏览器中间结果保留：默认 Chromium 未安装，首次未进入业务断言；使用已安装 Chrome 后出现 7 PASS、1 FAIL。
临时观察后单次主流程通过，但没有将偶发通过当作修复证据；新增时序反例先复现 1 FAIL，再实施修复并取得最终 9 PASS。
失败追踪保存在本地忽略目录 `evaluations/results/audit-fixes-20261016/browser-race-before/`；不随仓库提交。

另外扩大到仓库根目录执行 `ruff format --check .` 时，6 个历史 `examples/` 文件格式未通过；
它们不在 CI 应用检查范围，也没有在本轮改写。因此这里没有将整个仓库格式检查写成通过。

离线结果在 `evaluations/results/audit-fixes-20261016/demo.json`（本地忽略文件），run_id：
`006acd1c-9d69-45d3-8032-1cabee150672`。

| 指纹 | SHA-256 |
| --- | --- |
| implementation_sha256 | `15bc94f9e2833e39013549633c69db2810116297b6a5a243ba4dd934e0c502d9` |
| cases_sha256 | `294350b1a7da151d6d79143bd8135bc1a9d0d98c86da90e0e3bd5c17ad8073df` |
| fixture_sha256 | `7df046bcb9fdddeb8d91b28e2e7a437cb9cabe64ef01f7b95eaec27111fea20a` |

实现指纹沿用评估器定义，包含 app、evaluations 的 Python 源码与 uv.lock，不包含前端或 Markdown；R6 由独立浏览器回归覆盖。
此前 f3a12de 三个真实模型教学单例与上一批容器 CI 都是历史实现结果，分别保留在 [16 记录](16-usage-receipts-and-provider-check.md)和 [18 记录](18-python-delivery-validation.md)。

## 4. 复现入口

从仓库根目录执行，临时 PG 和浏览器需要允许本地端口，不能将测试 URL 指向个人或生产库：

```bash
AGENT_MODE=demo KNOWLEDGE_PROVIDER=local APP_ENV=test PYTHONDONTWRITEBYTECODE=1 \
  uv run --offline --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests

AGENT_MODE=demo KNOWLEDGE_PROVIDER=local APP_ENV=test \
  uv run python -m evaluations.run --mode demo --output evaluations/results/your-new-run/demo.json

COMMERCE_BROWSER_CHANNEL=chrome AGENT_MODE=demo KNOWLEDGE_PROVIDER=local \
  pnpm --dir frontend test:e2e

pnpm --dir frontend build
pnpm --dir frontend format:check
uv run ruff check app tests migrations evaluations scripts
uv run ruff format --check app tests migrations evaluations scripts
```

评估应使用新路径，避免覆盖既有证据；浏览器主流程会重新生成 docs/images 中两张截图，本轮验证后恢复了已有截图。
容器与公网验收遵循部署文档的隔离和维护条件，不在上述本地 PASS 范围内。

## 5. 文档与后续工作

README、TASK、架构、源码速查、评估、部署及学习系列已随行为更新。
下一轮优先在可丢弃 Docker 环境运行两种代理探针，再核对目标 HTTPS 链路；实际 RAGFlow 需用本组织样例和私有附件完成授权、来源与失败降级联调。
更大规模重构应基于这些回归逐步推进，不将本轮局部修复描述为整体生产验收完成。
