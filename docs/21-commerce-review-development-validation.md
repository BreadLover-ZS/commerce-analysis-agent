# 经营复盘开发与验证

日期：2026-10-16。本文描述当前工作区实现，不代表已提交、推送或部署。

## 1. 范围与交付

目标是先由个人使用可复现模拟数据，跑通经营复盘，再接入真实数据并逐步邀请用户。
采用固定流程：选择数据版本与周期 → 数据质量检查 → 确定性指标与贡献分解 →
受控候选解释与核对建议 → 校验 → 可选人工复核 → Markdown/PDF/JSON。

本轮新增经营复盘主入口，原库存/竞品研究保留在“库存与竞品研究（高级）”。
经营复盘服务端强制 `profile=commerce_review`、`sources=[sales]`、`strategy=workflow`，
必须绑定本人已确认的数据版本，不允许附带公开搜索关键词或扩大工具范围。

已实现：

- 九类固定种子模拟数据，CSV 模板、上传预览、错误行提示、确认入库。
- 所有者隔离的不可变数据版本、内容哈希和首份上传文件哈希、导入去重和配额。
- 本期/前期覆盖检查、商品启用日期、商品/品类净销售额变化贡献及加总对账。
- 程序生成指标事实与变化观察；模型仅选择证据允许的候选假设代码并生成核对建议。
- 专门的五章报告、完整证据账本、数据版本与模拟标记、人工复核与导出。
- 回归、离线与真实模型评估入口、隔离 HTTP 演练、CI 经营复盘入口。

尚未交付生产环境：本机没有 Docker，未提供目标 HTTPS 服务器；真实业务数据也尚未取得。
首版只支持固定 CSV；XLSX、平台订单同步、退货订单队列、下架生命周期和自动经营动作不在本轮范围。

## 2. 直接使用

按 README 启动后登录。默认进入“经营复盘（固定流程）”：

1. 选择“正常经营”等场景，点击“生成模拟数据”。种子固定为42；API允许显式指定种子。
2. 查看预览、默认周期、记录数量和限制，点击“确认使用此数据版本”。
3. 修改周期后等待数据质量检查；缺数可生成明确降级报告，非法日期不能提交。
4. 点击“开始分析”。可先勾选“导出前由我复核草稿”，查看依据后确认导出。
5. 拿到真实数据后，下载模板，替换内容并按实际性质调整“导入内容为模拟数据”选项。

数据性质由用户声明；系统不能验证上传内容是否来自真实业务。模板与生成场景都是模拟数据。
`demo` / `live` 是执行方式，与数据是否模拟分开记录：模拟数据可以调用真实模型。
未修改本地 `.env` 的默认 demo 模式，没有为运行工作台自动启用付费模型。

## 3. CSV 数据契约

UTF-8（可带BOM），列顺序固定：

```csv
sku,name,category,active_from,day,paid_units,refunded_units,revenue_cents,refund_cents
A,示例商品,日用,2026-09-01,2026-09-01,10,1,10000,1000
```

- 一行是一个商品一个记账日，日期使用 `YYYY-MM-DD`。
- 同一SKU的名称、品类和启用日必须一致；商品日期不能重复，不能早于启用日。
- 金额为非负整数分，件数为非负整数；单值不超过 PostgreSQL INTEGER 上限。
- 单文件读取上限2 MiB，1–100个商品，1–10000条记录，日期跨度最多366天。
- 商品启用日起每日应有显式记录；零销量需要零行，缺行不会补零。
- 退款可能跨期，允许当天退款金额/件数超过当天支付，报告提示核对归属。
- 商品停报/下架尚未建模。不要把停止报送隐式当作下架；否则会被视为缺数。
- 错误最多返回20处行号与安全描述；整个文件通过校验后才暂存，失败不部分入库。

目前页面经营CSV与“高级研究”的文档附件是不同入口。原 `app.cli import-commerce`
仍是管理员向旧业务表导入JSON的工具，不会自动创建新经营数据集。

## 4. 数据版本与权限

新增 `commerce_datasets` 表，迁移 `c21a01600001`，前置 `878d61a878e3`。
预览保存有界JSON，`committed=false`；确认只改变状态，已确认内容没有覆盖更新API。
同一用户、相同规范化内容与数据性质返回相同ID；文件行序不影响内容身份。
`file_hash` 保留首次暂存文件哈希，重复内容的新文件不会追加一份上传尝试日志。

列表、详情、质量检查、确认和任务登记均检查用户与租户。即使同租户用户也不能读取
他人的数据集。任务与产物沿用既有所有者校验。身份来自登录会话，不来自模型或上传文件。

每用户最多50个版本（含未确认预览）。暂存新预览时清理该用户超过一天的未确认版本；
已确认版本不自动删除。首版没有自助删除/归档接口，达到上限需管理员评估离线归档或配额策略。
不要直接删除仍供任务引用的版本。导入准入与幂等以用户行锁保护，SQLite使用现有短写事务。

任务绑定具体ID，计算始终读取同一不可变payload；本期、前期、商品、品类来自同一版本。
新版本导入及旧 `products/daily_sales` 更新不改变既有任务。这个保证适用于新经营复盘路径；
旧高级研究的销售与库存工具仍各自读事务，不提供跨工具统一数据库快照。

## 5. 指标与报告边界

确定性计算代码：`app/commerce/review.py`。
金额用整数分累加，Decimal格式化；变化贡献是本期净销售额减前期净销售额，商品与品类
分别可加总到整体变化金额。两期记录不完整或无适用商品时不输出变化贡献。
前期净销售额非正时保留变化金额，不生成变化率。支付件数为零时不生成退款件数占比。

净销售额未扣成本，不是利润；退款件数占比按记账周期统计，不是同一批订单最终退款率。
商品/品类较多时按净变化绝对值展示前四项，并在限制中披露；完整明细保存在证据JSON。
九类场景在 `evaluations/commerce_review_cases.json` 有手工推导的默认种子净销售额与可比性预期。
另有独立小数据基准：前期40元、本期38元；商品A贡献负10元、B正8元，合计负2元。

五章报告是：经营表现、变化贡献、退款观察、经营解读、行动建议。
前三章事实和经营解读中的观察均由程序生成，不允许模型替换、删除或改写。
模型 `ReviewNarrative.analysis` 只能选择 `allowed_hypotheses` 中的代码，程序渲染为
“待验证假设/待核对事项”；非法候选被拒绝。候选目录在 `app/commerce/hypotheses.py`。
这里是明确的产品约束：AI帮助选择待验证方向，不自由重述已知事实，也不证明候选原因成立。
建议仍由模型生成，必须有证据引用，并经过数字、已知方向反例和模型语义检查。
模型生成失败或不合格时保留确定性报告与限制。语义评审不是正确性证明，建议仍需人工核对。

## 6. 验证结果

最终结果与运行批次见本文末尾的验收记录。自动检查和逐条核对分别记录，初始失败不会删除。
本文“人工核对”指本次开发中Codex逐条对照报告与证据，区别于服务模型自评；尚无运营人员业务验收。
本地结果保存在忽略目录 `evaluations/results/commerce-review-20261016/`。

重要失败与修正：

1. `live-initial`：沙箱连接失败，未知用量后停止后续调用；未将模板回退算作模型成功。
2. `live-network-enabled`：自动9/9，人工7 PASS、2 FAIL。头部商品下滑时把退款占比上升
   当成退款金额上升；另一个场景无阈值却称退款水平较高。
3. 补充支付金额、退款金额、退款件数各自的变化量及前期值，增加方向与阈值反例校验。
4. `live-final`：自动9/9，人工7 PASS、2 FAIL。缺数场景仍出现周期/比较边界的矛盾表达。
5. `live-gated-final`：自动9/9，人工8 PASS、1 FAIL。正常场景仍把已有零贡献数据说成缺失。
6. `live-catalog-final`：正常场景通过，销售下降场景因模型评审将程序已计算的退款占比变化误判为无依据而降级，评估按首个失败停止。
7. 最终将模型语义评审限定于自由生成的核对建议，`semantic_scope=recommendations_only`；
   程序继续验证事实、观察和候选解释范围。最终批次见 `live-delivery-final`。

原214条、旧32条评估及历史真实模型单例的适用范围仍见20/18/16号文档，不能替代本轮验证。
HTTP演练为本机闭环demo，不能推导真实模型并发、线上容量或可用性。

## 7. 复现命令

从仓库根目录执行，模型评估仅连接已配置模型，数据库始终为临时SQLite：

```bash
uv run python -m evaluations.commerce_review --mode demo --output evaluations/results/my-review-demo
uv run python -m evaluations.commerce_review --mode live --output evaluations/results/my-review-live
uv run python -m evaluations.commerce_review --mode live --cases normal top_decline --repeats 2 --output evaluations/results/my-review-repeat
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
COMMERCE_BROWSER_CHANNEL=chrome pnpm --dir frontend test:e2e
uv run python -m scripts.load_smoke --profile commerce_review --backend sqlite --requests 20 --concurrency 4 --workers 2 --output evaluations/results/my-review-load.json
uv run --with pgserver==0.1.4 python -m scripts.load_smoke --profile commerce_review --backend postgres --requests 20 --concurrency 4 --workers 2 --output evaluations/results/my-review-pg-load.json
```

评估目录必须不存在。单次评估每任务模型调用最多6次、返回Token阈值20000、执行时限180秒，
首个自动失败停止；人工失败要单独保留。阈值不是绝对费用封顶，未知用量不得记为零费用。
评估不自动改写人工结论。`summary.json`包含实现指纹，`receipt.json`保存任务、用量和事件；
每场景另存输入、报告、证据和PDF。指纹不含前端、文档或部署配置，浏览器/部署需单独验证。

## 8. 升级与邀请用户前的门槛

先停止API和Worker并按部署文档备份数据库、检查点和产物，再执行：

```bash
uv run python -m app.cli init
```

它会迁移到head；不要在有真实数据的环境打开自动demo播种。确认升级成功再启动服务。
本地演示启动脚本会先迁移。新表进入既有SQLite/PG备份范围，但仍需在目标部署做恢复演练。
不要在业务库执行测试、负载脚本或降级迁移；降级此迁移会删除数据版本表。

邀请用户前仍需：实际Docker镜像与代理探针、目标HTTPS/Cookie链路、独立账户、真实数据
导入与人工报告核对、目标环境备份恢复、实际模型延迟/费用和故障告警。当前未提供这些环境，
因此只交付开发实现与本地验证，不宣称已开放真实试用或生产验收。


## 9. 最终验收记录

| 项目 | 最终结果 | 范围 |
| --- | --- | --- |
| 后端全量 | **251 PASS，50.84秒** | 隔离PostgreSQL16.2，含迁移、并发首写、备份、恢复和新增业务用例，无跳过 |
| 经营复盘离线评估 | **9/9 PASS** | 模型调用为零，九场景与数字/引用/版本契约 |
| 原综合研究离线评估 | **32/32 PASS** | 保留旧链路的业务契约；非模型质量 |
| 前端构建、格式与Python静态检查 | **PASS** | TypeScript/Vite、Prettier、Ruff；84个Python文件格式通过 |
| Chrome浏览器 | **11/11 PASS，23.4秒** | 3个本地demo真实API用例（含CSV错误/复核/PDF）与8个接口替身诊断；未验证live浏览器 |
| 真实模型九场景 | **自动9/9，逐条核对9/9 PASS** | DeepSeek现有配置，模拟数据；18次模型调用，不是实际运营验收 |
| 同实现重复运行 | **自动4/4，逐条核对4/4 PASS** | 正常/头部商品下滑各两次；事实文本一致，建议措辞有波动 |
| 隔离SQLite HTTP演练 | **20/20 PASS** | 并发4、Worker进程2，幂等重放+完成轮询+PDF下载与哈希；零模型调用 |
| 隔离PostgreSQL HTTP演练 | **20/20 PASS** | PostgreSQL16.2，同上；零模型调用 |
| 导出PDF内容检查 | **PASS（内容范围）** | 真实模型normal样例3页，五章中文标题可提取；未将此称为完整排版验收 |
| Docker/公网HTTPS | **BLOCKED / UNTESTED** | 本机没有Docker，未提供目标服务器；新增CI与探针入口不是运行成功证据 |
| 真实业务效果、业务人员验收 | **UNTESTED** | 尚无真实经营数据，候选原因与建议仍需业务确认 |

SQLite演练完成耗时P95约1.22秒，PostgreSQL约2.04秒；均是本机demo闭环，含排队与轮询，
不能推导线上容量或真实模型延迟。最终九个真实模型任务总耗时约3.49–5.53秒，重复批次约3.87–5.72秒；
仅是本次提供方与网络下的小样本，没有得到生产SLO或费用封顶结论。未配置Token单价，不写零费用。

证据目录：`evaluations/results/commerce-review-20261016/`（本地忽略目录）。
最终后端日志为`backend-delivery-final.log`，新离线批次为`demo-delivery-final`，
旧离线为`legacy-32-delivery-final.json`，真实模型为`live-delivery-final`与`live-repeat-final`。
两个真实批次的`manual-review.json`分别保存逐条核对结论与重复结果比较。
HTTP演练为`load-sqlite.json`和`load-postgres.json`。历次失败及降级结果仍保留。

最终实现指纹（应用/评估Python、迁移与uv.lock）：
`c328a37b62f683038bbb0220d1b7b2c77ad37964a90f1a1c2a1d14886d6046f5`。
九场景真实批次run_id：`d7808cf9-dcd7-4572-9816-81221c6c9e6b`。
该指纹不含前端、文档、测试、CSV预期JSON或部署配置，不能替代相应独立检查。
