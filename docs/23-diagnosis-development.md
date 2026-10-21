# 经营诊断增量：实施与验收记录

开发契约：`docs/commerce-diagnosis-spec-v1.1.md`（用户提供的 v1.1 原文）。
基线：`47bfc1ba1546ab24a9059d014687961e71f438bc`；2026-10-19 开始实施。

## 范围与当前状态

完整目标仍为 G0—G7；此文件记录实际完成证据，不将部分实现视为完成。
当前范围按2026-10-20用户明确调整：**本轮执行G0—G6，G7不执行**。已有运维代码保留；目标部署、本人练习和G7验收不作为本轮门槛。
真实模型已获DeepSeek/deepseek-flash整轮累计300万token授权，应用仍保持demo。首段18个Task及失败已保留，续评只执行未运行的完整案例。

| 阶段 | 实现工作 | 当前证据与剩余出口 |
| --- | --- | --- |
| G0 | 基线、60例/分集、独立预期与目标表 | 已固定；真实成本/延迟按当前模型回执记录，不凭空声明SLA |
| G1 | 不可变输入、显式两期、受限工具与规范缓存 | 销售/库存/资料权限、独立数值、SQLite/PG测试已覆盖 |
| G2 | 有界诊断、确定性观察与停止 | 协议及真实模型轨迹已有记录；完整保留集与语义核对正在补齐 |
| G3 | 调用账本、fencing、恢复、澄清与版本路由 | 真实PG/SIGKILL、重复答案/旧批准/取消已覆盖 |
| G4 | 库存版本与代理、清单检索、未核验解读 | API/v2图/报告及真实浏览器闭环已实现；语义效果随真实评测另记 |
| G5 | 创建、步骤、澄清、复核、反馈、同输入对照和导出 | 17项Chrome，其中6项诊断真实API/Worker；PDF与移动端已查看 |
| G6 | CLI、独立oracle、断言、配对、完整记录和独立核对 | 40个开发/回归问题59次demo自动通过；99个Task已完成并逐项自动复核，诊断38/60未达标；详见28号文档 |
| G7 | 运行与个人学习交付 | 用户明确本轮不执行；已有工具保留，未验部分不算完成 |

## 已核对的复用边界

- 原 `commerce_review`：TaskRequest → Repository.create_task → Worker →
  research.graph 的 plan/collect/draft/validate/review/export。
- 销售数据：CommerceDataset（所有者、确认状态与内容哈希）→ dataset_review →
  calculate_review；关键事实与贡献由程序计算，模型仅选候选解释/建议。
- 保留 Repository 任务队列、预算、租约与 LeaseFencedSaver；新图复用外层 task 线程。
- 旧 metric_scope 仅按工具/周期，新增诊断查询必须使用包含规范规格的独立身份。
- 旧 RuntimeModelMeter 不重复计数；新模型账本接入前不能把新循环开放为可靠入口。

## 继续开发规则

1. G1 工具先建立规范结果与调用 attempt，成功结果和 Evidence 同一事务写入。
2. G2/G3 交错实现；只有持久恢复与等待契约齐全后才开放诊断任务创建。
3. 原 profile 的校验、报告与导出保持回归；不修改依赖锁来顺手升级框架。
4. 每个 GV 分别记录 PASS / FAIL / BLOCKED / UNTESTED，缺证据不能勾完成。
5. 所有实际测试结果仅属于测试代码覆盖的版本与环境；付费模型和生产环境另验。

## 2026-10-19 首批实际实现

- `app/commerce/calculations.py` 提取纯聚合与显式两期校验；旧
  `calculate_review` 仍推导相邻前一期，`calculate_periods` 接受等长自定义前期。
- `app/diagnosis/inputs.py` 与 `schemas.py` 固定销售哈希、周期、授权范围、模拟标识与算法版本。
  `POST /api/analysis-inputs` 按规范内容幂等；所有权/确认状态/实际内容哈希在读取与执行时复验。
- 五个销售工具已实现：inspect_dataset、compare_periods、rank_contributors、
  drilldown_series、analyze_refunds。禁止身份、任意路径、周期覆写、未知指标和越界筛选。
- 贡献按整数分对账，含完整明细与“其他”，支持有符号超过百分之百的贡献比例；
  缺行、明确零、未启用、基期非正、退款分母为零分开呈现；日与品类/SKU下钻均计算真实结果。
- `tool_query_results` 与 `tool_invocations` 分别保存规范结果和尝试。
  成功 Evidence 与结果/调用状态原子落库，规范身份包含有效 SKU、两期、来源与版本。
  使用独立 `diagnosis_metrics` 证据种类，避开旧 writer 的 metric_scope 去重。
- 缓存重用前重新校验权限和来源哈希；成功恢复不重扣次数，未完成确定性调用的重算另扣预算、保留 UNKNOWN。
  模型摘要最多 32 KiB，完整结果保留在授权证据；截断后保留引用闭合或返回明确摘要截断说明。
- `model_invocations` 与 `model_calls.py` 实现稳定调用身份、预占/STARTED、响应及 usage 原子提交。
  校验失败保留已知 usage 与响应 hash；修正使用不同 attempt。
  接管时成功输出直接复用；未落库响应的调用保留预占、转 UNKNOWN 并禁止新模型请求。
- 新边界共用 Repository 的预算与 usage 原语；诊断模型克隆时移除旧 RuntimeModelMeter，
  旧 profile 保持原计量回调，避免双重扣次。SDK 重试必须为零。
- 新迁移为 `d31019000001` 和 `d31019000002`；已有数据的诊断表拒绝自动 downgrade 删除，
  回滚应停用入口、保留记录或按一致备份恢复。空数据库正反迁移已验证。

首批时未开放诊断入口；下述第二批已接入开发用 API。前端仍未接入，正式放行未完成。

## 首批验证证据（历史批次）

初次旧回归：231 PASS、17 SKIP；3 条真实 HTTP 测试因沙箱禁止 TCP bind 失败。
在允许本地端口后复验对应测试组 7 PASS。未修改测试以绕开端口验证。

| 批次 | 命令/环境 | 结果与适用范围 |
| --- | --- | --- |
| 销售工具/旧复盘 | `.venv/bin/python -m pytest -q tests/test_diagnosis_tools.py tests/test_commerce_review.py` | 70 PASS；其中新工具 34 条 |
| G1 PG 边界 | `UV_CACHE_DIR=.uv-cache uv run --frozen --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests/test_diagnosis_postgres.py`（G1 时为 4 条） | PostgreSQL 16.2，非超级用户隔离 schema；4 PASS |
| G1 全量 | 同 PG 脚本运行 `tests` | 289 PASS；模型账本实现前的历史批次 |
| 模型边界与旧 Meter | `.venv/bin/python -m pytest -q tests/test_diagnosis_model_calls.py tests/test_usage_validation.py tests/test_model_adapter.py tests/test_diagnosis_tools.py` | 80 PASS；脚本协议，不是真模型 |
| 当前全量 | `UV_CACHE_DIR=.uv-cache uv run --frozen --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests` | **304 PASS**；含 6 条新诊断 PG 测试与 13 条新模型协议测试，无跳过 |
| 静态检查 | Ruff check、format --check（app/tests/migrations/evaluations/scripts）与 `git diff --check` | PASS |

模型故障测试中的 SystemExit 是可控故障窗口模拟；不能称为已完成真实 SIGKILL/重启后的新图恢复验收。
上述 PG 验证覆盖账本/输入/工具事务与行锁；没有覆盖尚未实现的新图、人工等待或生产部署。

旧流程离线基线保存在以下目录（忽略 Git，保留本地回执/报告/证据）：

- `evaluations/results/diagnosis-g0-fixed-20261019-01`：runId
  `805632ec-dc75-4dcf-8374-2b0b82e96b52`，9/9 模拟经营场景 PASS；
  implementationHash `847e602bb03ed2abccf9c5d4babb75f6b11ab0512fc0df1859d54461a2748fc7`。
- `evaluations/results/diagnosis-g0-research-20261019-01`：runId
  `edeb3557-7608-443c-b468-5ea154112797`，32/32 原研究离线契约 PASS；
  implementationHash `dcd57e465049a11aa5483592c04af0a7fe99006cc0f3686f507bee9eb2f3f8ca`。

两次基线位于本轮中间实现版本；不能冒充最终新诊断效果评测。
固定流程回执包含数据/报告/证据与模型计数，demo 未发真实模型请求。

依赖未升级：Deep Agents 0.7.25、LangGraph 1.2.15、LangChain 1.4.5、
SQLAlchemy 2.0.49、Pydantic 2.13.4、FastAPI 0.136.1。
`uv.lock` SHA256：`028eb8d6e37efed27c31483f54d5803fe9f40e5a123e87b50cd0a1c1fe3451a9`。
输入契约原文 SHA256：`b55ffef94ad28305f249e4afc68801f90986372380a214180809481793b0e9de`。

### GV 状态（只按当前测试覆盖）

- GV01/02/03/04/21/22：销售工具层 PASS；完整产品链待 G2/G5。
- GV05/26：销售所有权、范围、撤权与缓存门禁 PASS；库存/文档清单与任务来源交集待 G4。
- GV10/11/12/14/23/24/27：账本/工具/模型边界的协议和 PG 子项 PASS；
  整图恢复、取消与循环预算组合仍 UNTESTED。
- GV06：工具严格参数子项 PASS；模型非法提议的修正/停止循环待 G2。
- GV07/08/09/13/15/16/17/18/19/20/25/28/29/30：整体 UNTESTED。
- 真实模型效果、60 次保留集、前端新闭环、目标环境：UNTESTED，未调用任何付费模型。

## 下一批实施入口

1. 补 G0 的 caseId/分集/独立预期与版本化指标目标表，不把场景标签或答案送入模型。
2. 按固定 graph/schema/prompt/tool 版本扩展 Task、steps、findings、input_requests 和反馈表。
3. G2/G3 交错接入 LangGraph 的基线→提议→授权→单工具→程序评估→停止/澄清循环；
   当前可直接复用 DiagnosisToolGateway 与 DiagnosisModelGateway。
4. Worker 按 profile/版本路由，增加 waiting_input/expired、TTL、CAS 恢复、报告版本与批准身份。
5. 必须经真实 PG 故障/并发验证后才开放新任务入口，随后继续 G4—G7；目标未完成。

## 第二批：销售诊断循环与可靠继续（2026-10-19）

已接入 `commerce_diagnosis + diagnostic` 请求与既有 Worker；显式日期与 bundle 核对，
省略或仅给一个日期时从 bundle 派生，旧 profile 的幂等哈希保持兼容。
新增迁移 `d31019000003`，包含 Task 版本、执行分段与恢复次数、步骤、假设、人工输入和反馈。

`app/diagnosis/graph.py` 实现输入、两次基线、提议、授权、单工具、程序评估、停止/澄清、
报告、复核和导出。8 步与 10 工具是持久硬门禁；缓存、拒绝和失败仍占步骤，
连续无信息或无效提议停止。不同 Top-K 不靠新 ID 伪装新信息。
模型只选下一步；事实和候选支持/反驳/不足由程序生成，并在发布前按固定源重算。
明确零变化不支持“贡献集中”的判断；金额和占比方向分别核对。

任务受理时固化工具、模型、Token、执行时间和恢复额度；配置上调不能扩大发行时的预算。
demo 任务不因部署模式改变或注入真实 SDK 客户端而转成付费调用。
demo_policy、scripted_protocol 与 live_model 分开标记；本轮只有前两者的测试证据。

`waiting_input` 与 `expired` 已接入活跃配额、领取、取消、TTL、SSE 与 API。
澄清答复按 interruptId/expectedVersion CAS 落库，只缩小授权 SKU/品类或目标；
重复相同答案幂等，不重复推进。等待不计执行时限，恢复保留原消耗预算，正常人工继续不占故障接管额度。
步骤/模型结果已落库但 checkpoint 落后时复用；结果未知保留预占并停止新模型调用。

报告有独立 schema，程序生成事实、观察、过程、缺口、建议和版本；复核绑定 reportVersion/hash。
旧批准不能导出修改后的报告。产物先写临时键，再在受 fencing 保护的事务中成组发布；
取消后的孤立文件不可下载，已发布 PDF 在恢复时核对哈希后复用，避免重建 PDF 的时间戳改变内容。
新增 diagnosis/clarifications/feedback API；详情、证据、事件、流和下载都复验固定输入访问权。

### 第二批实际验证

- 最终命令：`UV_CACHE_DIR=.uv-cache uv run --frozen --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests`。
  **349 PASS**，PostgreSQL 16.2，非超级用户隔离 schema，无跳过；Ruff/format/diff 检查通过。
- 新增图/等待/报告测试 27 条、SQLite 真 SIGKILL 7 条、新 PG 图/恢复/人工 CAS 测试 11 条。
  PG 另有首批输入/工具/模型边界 6 条；新图七种 SIGKILL 窗口在 SQLite 与 PG 均执行。
- 窗口包括工具成功后、模型发送前/响应落库前/输出落库后、输入记录与 checkpoint 两种错位、
  产物登记后；验证不重扣已成功调用、未知调用不重试、输入只推进一次及产物身份不变。
- 曾出现全量 `346 PASS / 1 FAIL`：旧迁移 fixture 用当前 ORM 写旧 schema。
  改为反射旧表插入实际旧记录，再升级并运行 Alembic schema check，补验旧幂等请求；未删除或跳过该测试。
- 旧固定复盘与研究在第二批中间指纹下分别 9/9、32/32 demo PASS，保存在
  `evaluations/results/diagnosis-g2-fixed-20261019-02` 和 `diagnosis-g2-research-20261019-02`；不是 G6 正式公平对照。

### 可复现实例

新增 `python -m scripts.diagnosis_demo --scenario sales_decline --output <不存在的目录>`。
该脚本建立隔离 SQLite、运行真实 Worker 并保留输入、独立金额/覆盖预期、版本、步骤、事件、证据和 MD/JSON/PDF。
不加载模型凭据或业务 DB，实际模型请求为零；它验证执行和数学契约，不证明真实模型决策质量。

- 总体下降：`evaluations/results/diagnosis-demo-20261019-01`，runId `a5e8822f-e727-439b-b56b-925ed5f58ad7`，PASS。
- 单 SKU：`evaluations/results/diagnosis-demo-scoped-20261019-01`，runId `f03526aa-3416-43b8-bc63-f1779e73dd16`，PASS。
- 两份回执的主实现指纹均为 `a2bcbf8b0e3fd890efa4471f386a716f63d78f5a48eda73cbfce120672e72160`；脚本入口另记自身 hash。
  PDF 已通过生成/下载结构检查，渲染排版 QA 与浏览器新闭环仍留 G5，人工业务核对仍 UNTESTED。

### 第二批结束时的剩余（历史）

G0 的 60 条分集及版本目标表；G4 的版本化库存与文档；G5 前端和真实 API 浏览器闭环；
G6 正式对照/真实模型保留集；G7 一致备份、运行与学习交付均未完成。
G4 改动模型 schema、Prompt 或工具目录时必须同时处理旧图兼容路由，不能把旧 checkpoint 直接装入新契约。
真实模型与目标环境仍 UNTESTED，不因此停止可以独立推进的开发。

## 第三批：固定问题集与版本化扩展来源（2026-10-19）

### G0 问题与门槛

- `evaluations/diagnosis_cases.json`：60 条 caseId，开发/回归/保留各 20 条，含原始记录、
  允许范围、两期、独立金额预期、必要工具、停止/终态、禁止结论、故障模式与基线分组。
- `diagnosis_cases.py` 校验编号/分集/输入唯一性与独立原始记录答案，
  `business_input()` 显式排除答案、facet、split、caseId 和故障 harness。
- `diagnosis_targets_v1.json` 固化 90% 目标、关键门禁 100%、8/10/12 上限和 20×3 保留集要求。
  真实 P95/费用目标为空；本地固定流程 9 例的中间版本测量不能推成线上 SLA。
- 新增 30 条问题集结构/独立金额测试；仅开发/回归样本运行指标检查，
  保留集只做完整性检查，未运行 Agent 评分，也未用保留集调 Prompt。
- 固定问题集 canonical SHA256：`843f03b9fc76a87d343ab689de0df5c03441eddec1f332b344c51fc9178c0a7f`。
  文件 SHA256：`944d24f429cae1065843200ec5cb3e78096baca1ed8b7a45e4d2e15d8c394866`。
  明细与评测边界见 `evaluations/diagnosis_cases.md`。

### G4 来源与纯算法基础

- 新迁移 `d31019000004`：库存数据版本、文档清单与 bundle 的显式 FK 绑定。
  已有数据的来源表拒绝自动删除回滚；销售与库存的过期预览清理均跳过被输入包/旧复盘引用的版本。
- `source_schemas.py` / `sources.py`：库存 CSV 严格列/类型/唯一 SKU、预览确认、内容与文件哈希、
  所有者/租户/额度；文档清单绑定文档、chunk、内容哈希和原有会话范围，每次读取重验。
- 组合输入冻结库存时点检查策略；混合真实声明/模拟来源时按来源保留标识，整体只要有模拟资料即披露。
  未绑定库存不从旧 products 表补数；未知销售 SKU 在组合准入时拒绝。
- `extensions.py`：完整销售窗口的支付件数代理、库存覆盖代理与提前期整数比较；
  零支付、缺行、过旧/未来快照、缺库存或提前期分开编码。退款是否回库未知，不自动减/加库存。
- 规则检索只处理传入的固定 manifest 片段；返回逐字窗口、身份、hash 和未核验冲突候选。
  引用存在不等于规则含义、冲突或实际经营原因已证实。
- `diagnosis_v1_contract.json` 固定旧销售 v1 的 Step/Report Schema、目录与提示词哈希，
  新增兼容性测试防止扩展时改变旧模型调用输入。

第三批结束时，这些函数只在来源与纯计算层通过测试，**尚未加入 Agent 目录、扩展图版本、报告 schema 或新导入 API**。
不能据此宣称库存/规则产品闭环、真实模型理解或语义冲突识别通过。

### 第三批验证与后续

- 最终全量：`UV_CACHE_DIR=.uv-cache uv run --frozen --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests`：
  **396 PASS**，PostgreSQL 16.2，无跳过；Ruff、format 与 diff 检查 PASS。
- 本批新增 47 条：问题集 30、v1 兼容 1、来源/纯算法 12、真实 PG 来源 4。
  PG 验证并发去重、同租户所有者边界、文档片段变更及撤权后的引用保留。
- 新同租户 fixture 曾漏导入 uuid4 导致对应本地/PG 测试失败；补齐导入后原断言保留，局部与完整回归均通过。

第三批计划：先把 v1/v2 契约和兼容构建路由接好，再接入库存/规则工具、受限规则解读、版本化报告及导入 API。
新工具目录必须是 bundle 能力、task sources 与注册表的交集；来源会话范围继续校验。
v1 的 Schema/Prompt/目录不能因新增 v2 定义而变化，旧成功模型输出仍须能按原身份复用。
随后继续 G5 界面、G6 正式运行器与独立审查、G7 运行/备份/学习交付；整项目标仍未完成。

## 第四批：库存/规则 v2 与产品界面（2026-10-19）

### 扩展后端

- `contracts.py` / `schemas_v2.py` / `planner_v2.py`：新任务明确选择 v1 或 v2，未知版本拒绝恢复。
  v1 Step/Report Schema、工具目录与 Prompt golden 原样通过；没有用新版本解释旧 checkpoint。
- `inventory_risk`、`search_rule_documents` 进入同一个网关、规范查询缓存和调用账本，
  目录限定于任务实际绑定且选择的来源。库存结果绑定 sales+inventory hash，规则结果绑定 manifest hash。
- 新库存预览/确认/列表/模板、文档可见列表与 manifest 创建/读取 API；
  文档任务在准入与每次工具读取时继续校验原会话权限。
- 库存事实绑定快照日期；v2 可呈现 `units_per_day` 代理。缺数、零需求、过期、未来和不完整新品窗口不推成安全结论。
- 规则逐字引用、未核验冲突候选与带现有引用的模型解读分栏保存；解读只能 unverified。
  报告门禁重算原始来源，拒绝改数值/快照/原文/解读内容或引用。销售缺数不阻止独立引用规则，但不展示缺行伪零。
- v2 新信息判断包括库存状态与原文窗口；同一查询成功结果在 lease 接管后复用，未重新扣调用。

最终全量：`UV_CACHE_DIR=.uv-cache uv run --frozen --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests`，
**419 PASS / 0 SKIP**，PostgreSQL 16.2，98.01秒。本批在397项基础上新增22项，其中18项本地扩展、4项PG扩展。
PG来源组共8项通过，覆盖真实Worker/API/复核、来源边界、去重、接管与库存提交后的真实SIGKILL。
Ruff、format、diff 检查通过。

保留的失败：第一轮419项为 **418 PASS / 1 FAIL**。
v2 把“那个商品的库存”中的“商品”当成销售贡献目标，澄清后多调用一次排名。
已区分库存问题的对象名称与明确排名意图，原3次工具调用断言保留；局部19项及完整419项重新通过。
此前新增就绪门禁也已验证：只迁移到旧来源结构时 ready 返回503，升级到 head 后返回200。

### 前端接入与浏览器状态

保留固定复盘为默认入口。新增经营诊断配置，确认销售/库存数据、选择原会话规则资料，显式固定两期与SKU。
报告页展示事实、步骤、观察、停止原因、原文与未核验解读；支持澄清、具体报告版本复核、取消、反馈和继承固定输入的新追问。
详情按任务ID重新读取诊断投影，人工操作等 task/state/report 版本一致后才开放；反馈不改变原报告。

前端 `pnpm build`、`pnpm format:check` 通过。新增3项Chrome真实API闭环全部通过，19.6秒：
销售事实/证据/反馈/刷新/追问；库存与规则导入/澄清刷新/版本复核/PDF下载；非法周期与等待取消。
接口没有mock，使用临时SQLite、独立Worker与demo策略，不是模型质量或生产证据。

首轮3项失败保留：浏览器暴露异步上传后的旧闭包可能重复创建会话，后续manifest按权限正确返回404；
改为同一Promise复用会话创建。另两处fixture等待问题改为等待确认数据实际返回，并使用现有API的准确错误文案。
没有删除任何业务断言或放宽权限门禁。最终全部旧/新浏览器回归 **14 PASS**，Chrome，38.8秒。
其中旧诊断错误展示测试含接口mock，不能把全部14项都算真实API闭环；新增3项与旧复盘/研究产品路径使用真实临时API。

实际下载的库存/规则报告共3页，以捆绑PDFium逐页渲染并查看，中文、分页、数值、固定快照和未核验引用可读，无裁切/重叠。
捆绑Poppler因Adobe-GB1映射查找失败产生空白渲染；改用已安装PDFium核对，未把空白页当验收通过，也未据此改写业务报告。
桌面规则/观察截图与390px移动端长页保留，浏览器检查页面没有横向溢出。
样例不覆盖100 SKU极端报告的全部人工排版；原始证据始终保留完整明细。

本批本地证据：`evaluations/results/diagnosis-g4-g5-local-20261019-01`，含通过/失败日志、实际下载PDF、界面截图及source manifest。
实现+测试指纹：`c300c3bb36a990b3bc754323184a63962680247f26ec719a963e0e60ceb1156a`。
这不是G6正式比较或真实模型评测。报告语义仍按未核验状态保留。

第四批结束时尚待补齐的产品出口：提交任务前显式核对组合输入的自定义两期覆盖/不支持指标，
以及G6正式同输入对照结果视图。当前诊断在执行基线中检查覆盖，前端配置说明不能替代提交前的组合质量预览。
因此本地闭环通过不等于G5全部需求或G0—G7整体完成。

源码调用链、版本、账本身份、API顺序及本人独立练习见 `docs/24-diagnosis-source-map.md`。
练习是待本人完成的学习交付，不据此声称已经独立掌握。G6正式评测与G7运行交付继续推进，整项目标仍未完成。

## 第五批：提交前质量核对与独立数值oracle（2026-10-20）

### 固定组合的提交前核对

`GET /api/analysis-inputs/{id}/quality` 在每次读取时复验固定来源、权限与内容hash，
按实际自定义两期和授权SKU计算覆盖；返回库存可计算/缺口状态、文档清单规模和不支持指标，
不返回规则全文、不创建Task或调用账本、不扣工具/模型额度。
网页先显示两期、来源性质、缺行及不支持的利润/转化率等，再由用户勾选确认。
数据、周期或范围与已核对签名不同则禁用开始按钮，当前组合需重新核对。
任务创建仍在服务器重新授权，不把客户端的确认当作权限凭证。

新4项后端测试已通过，随后完整隔离回归 **423 PASS**，PostgreSQL 16.2，142.69秒，无跳过。
新增第4条诊断真实API浏览器用例验证：预览不发Task请求、范围/周期变化后重新确认、缺行可见及390px无横向溢出。

### 浏览器故障排查与兼容修复

| 项目 | 已核对内容 |
| --- | --- |
| Error | 预览非法周期后再次点击按钮超时；另两轮完整套件分别在旧研究页类型切换、库存任务提交后出现长时间停滞，各14/15通过 |
| Expected | 保留原30秒测试超时，错误可修正重核对，任务202后转入持久详情 |
| Cause | 第一处trace显示装饰性loading图标改变按钮可访问名称，已固定label；后两处trace中目标元素/完整202响应已存在，不能诊断成权限或服务没有返回。另确认React19.2.6+AntD5.29.3默认imperative renderer从react-dom读取已移除入口，控制台发出兼容警告；页面停滞与该缺口的唯一因果关系尚未证明 |
| Fix | `antdReact19.ts` 在现有版本上用官方v5 hook和react-dom/client.createRoot适配，并延后清理临时root；不升级依赖，不删断言或增大超时 |
| Prevention | 原真实交互路径新增兼容警告为空的断言；保留失败日志，后端压力与浏览器套件分开执行 |

参考[Ant Design v5 React19兼容说明](https://5x-ant-design.antgroup.com/docs/react/v5-for-19)，
实现同时核对了本地 `config-provider/UnstableContext.js`、`rc-util/React/render.js` 和类型声明。
修复后完整Chrome套件 **15 PASS**，58.3秒，其中4项新增诊断走真实API/Worker。
这是一次完整回归证据，不据此宣称已证明所有停滞的唯一根因或生产稳定性。
移动端预览截图：`docs/images/diagnosis-preflight-mobile.png`，实际查看日期、覆盖表、缺口与勾选框可读且无裁切。

### G6独立数值核对基础

`evaluations/diagnosis_oracle.py` 只使用标准库和原始fixture字典，未导入生产SalesCube、DiagnosticMetrics或报告校验器。
对销售汇总、退款分子/分母、贡献/TopK对账、日序列和库存代理建立第二套数值检查，
拒绝未知工具、额外事实、错误单位/金额、越界过滤与错误库存阈值。
11项局部测试通过，包含“伪造raw_value与格式化value彼此一致仍必须失败”的反例。
这只是评测底座，不是已完成正式CLI/全部轨迹断言、配对基线或真实模型评测。
最终434项完整隔离后端回归全部PASS，无跳过，PostgreSQL16.2，161.72秒。
过程中先有432 PASS/2 FAIL：一个产物复用与一个手动SDK配额测试的5秒租约到期；
对应非到期场景改用明确有效租约，子进程继承父配置，30项恢复/协议定向回归通过。
随后433 PASS/1 FAIL来自旧快照SIGKILL fixture，保留DB显示启动到恢复约185秒，原180秒截止已耗尽。
父子仅在该非预算测试中使用相同有限预算，接管时不重置deadline；两项定向通过后完整434通过。
生产限额没有改变，显式expire_lease、旧token拒绝和运行预算测试保留原断言。

本批回执：`evaluations/results/diagnosis-preflight-oracle-20261020-01`，保留以上失败/通过日志、schema版本、source manifest和界面/PDF。
实现+测试指纹：`48184c26f01cd8d2f08c585bb4e6dc7c8d6bf0cf6be1e8cead898cfc117d6710`。
新下载的三页PDF已再次逐页渲染查看，固定快照、引用和未核验边界可读，无裁切或重叠。
该本机测试批次不是G6逐例业务质量评估；正式运行器仍需单独保留每例请求/来源/轨迹/报告及独立审查。

固定60例文件未改，SHA256仍为 `944d24f429cae1065843200ec5cb3e78096baca1ed8b7a45e4d2e15d8c394866`。
未执行保留集Agent，也未用保留集调Prompt；本批真实模型请求仍为0。
逐项需求索引新增 `docs/25-diagnosis-acceptance-matrix.md`，明确GV08/19/20/29/30等尚缺部分，整项目标不改。

下一步：先补固定复盘对相同显式两期/授权范围的兼容适配，再实现正式评测CLI与严格断言，
随后接对照视图、孤立产物清理、一致备份恢复及运行交付。旧v1与新v2继续守回归。

## 第六批：固定复盘的同输入适配（2026-10-20）

为G6提供原 `commerce_review` 基线的显式组合适配，而不拿默认相邻前期与诊断的非相邻前期混比。
`TaskRequest` 新增允许固定复盘选择已有 `bundle_id`；仅sales+workflow，若同时给dataset或日期必须与组合一致。
仍绑定原销售版本，不复制/改期原始数据；原dataset-only入口保持默认相邻前期与旧请求hash。

`review_inputs.py` 在每次工具/模型额度预占和缓存读取前复验组合身份、源hash、用户/租户/范围。
固定报告记录bundle/source hash、实际前期和scope，下载/事件/详情在来源撤权后拒绝。
在途调用的已知usage仍可在当前lease下登记，撤权不应擦除计费记录。
纯 `calculate_periods` 在完整数据验证后选择授权SKU，允许该SKU没有记录，避免过滤成空集后被CSV准入schema误拒绝。

新组合复盘固定graph/prompt/schema/tool版本，未知版本拒绝恢复；与诊断使用同一个预算冻结函数和上限。
同时修正固定输入幂等：部署预算/版本变化不把相同客户端重试改成新任务，存储参数hash损坏不能借此复用。
第一次新组为17 PASS/1 FAIL，暴露直接hash命中路径尚未检查存储参数完整性；补齐后原损坏预算反例保留。

当前18项本地/PG测试全部PASS：原固定流程与诊断实际Worker使用同一bundle/源hash/非相邻前期/仅B范围，
手算700元、500元、差额200元一致；另测空记录授权SKU、冻结预算、重复/并发准入、撤权缓存/usage、
版本门禁、当前租户、参数错配及下载权限。旧入口71项局部回归也通过。
完整452项隔离后端回归PASS（无跳过，PostgreSQL16.2，168.67秒），完整Chrome 15项PASS（54.3秒）。
同输入基线的两页PDF已逐页渲染核验。回执见 `evaluations/results/diagnosis-bound-baseline-20261020-01`，
实现指纹 `f1eaa4f1f98dac9c36ae81960d6658421a07a2b10aa1b8cd560b7122de284863`；保留固定/诊断任务、轨迹、证据和产物。
这里证明同输入适配可执行，不是正式60例、真实模型效果或收益结论。

## 第七批：正式评测器、运行维护与同输入视图（2026-10-20）

G6现有可执行CLI，见[26号文档](26-diagnosis-evaluation.md)。固定60例及分集未改，所有业务输入与评测预期隔离。
独立标准库oracle按原始记录检查销售、完整事实目录与库存；逐项断言检查权限、版本、数值、引用、能力及停止边界，未知断言直接拒绝。
固定基线与诊断使用同包、两期、范围和预算；范围经过澄清变化时明确标为不可直接比金额。
故障harness单列为gate；真实SIGKILL/PG恢复仍由集成测试证明。独立语义审查有哈希绑定的导入入口，自动检查不填写业务正确率。

开发20例产生32次执行，回归20例产生27次执行，共40个独立问题、19个共同配对、8个新增能力、13个门禁，自动检查全部通过且供应商请求0。
正式源文件hash仍为 `944d24f429cae1065843200ec5cb3e78096baca1ed8b7a45e4d2e15d8c394866`。
回执：`diagnosis-g6-development-20261020-02` 与 `diagnosis-g6-regression-20261020-02`，
实现指纹 `4247685b7812bbe59659c84ed36412227454073401b7a60a99fcbd1a1f203fc6`。这些不是保留集或真实模型业务通过率。

G7补齐新诊断的停服审计、协调备份恢复、孤立文件dry-run/apply与隔离负载，见[27号文档](27-diagnosis-operations.md)。
业务DB、PG checkpoint与产物在同一维护窗口保存，恢复发布前核对版本、来源内容、调用账本、usage、报告和文件hash。
覆盖v1/v2、等待澄清/复核、取消、完成与UNKNOWN；PG的D3→head维护迁移后旧等待任务按原版本继续，新v2仍可准入。
清理保护登记版本、活动任务、近期文件及符号链接，只在停服显式apply时删除至少24小时的孤立文件，不给Agent删除权限。

PG16.2教学负载：双独立Worker、并发4、20提交/20完成、真实模型请求0、总计10.435秒；
提交P95 30.27ms、完成P95 2109.34ms。回执 `diagnosis-g7-load-20261020-01.json`，
run_id `107b1f4f-5e29-4bda-a3d7-8c106df4bd72`。这是本机demo，不是业务SLA、线上吞吐或目标部署验收。

G5新增同输入对照组件：本人历史任务经详情/证据权限读取后比较bundle、两期、最终范围、模式和预算，展示销售事实、工具/模型/token/耗时。
费用未知显示未知；历史模型配置不足时标明不可确认。用户可显式创建相同输入的固定基线；不自动改变默认入口或宣称Agent收益。
真实API浏览器覆盖创建、刷新、390px移动端、澄清后A与初始AB范围差异，截图 `docs/images/diagnosis-comparison-mobile.png` 已实际查看。

最新完整隔离后端 **491 PASS、无跳过**，PG16.2，117.36秒；完整Chrome **17 PASS**，52.7秒，其中6条诊断走真实API/Worker。
前端构建和格式检查通过，G6当时定向39 PASS。汇总 `evaluations/results/diagnosis-g6-g7-local-20261020-01/receipt.json`，
汇总实现指纹 `d945a790c0c8a8048293c5d217809d7ce1a9f096b802c22502338565e3606d08`。
该回执说明7个前端文件是在491项后端与59次G6执行之后变更，后端当时一致；最新token预算补丁在该回执之后，不能继承旧覆盖。

失败均保留：最初R15故障fixture没有真正注入澄清；库存统一日期未展开每行；初版grader将v2注册版本与内部算法版本、Task授权来源与工具依赖hash混淆，造成4个误拒。
先保留原失败，再修正评测协议与判定，未放宽数值/引用门禁。初版备份测试把目的目录放在源运行目录内，被保护规则正确拒绝，改为同级隔离目录。
前端构建曾暴露AntD5 Alert使用不支持的title属性，改为既有message API；未升级依赖或删除原浏览器断言。

## 第八批：整轮token硬预算与真实保留集（2026-10-20）

用户明确允许沿用项目配置DeepSeek、`https://api.deepseek.com`、`deepseek-flash`，并授权**整轮累计最多3,000,000 token**。
应用 `.env` 保持demo，评测只在进程内读取指定模型配置与密钥，不在参数/日志打印密钥，不连接业务数据库。
整轮预留、实际usage结算及UNKNOWN保留采用同一个RequestBudget，基线/诊断、不同案例及重复都累计；journal在发送前fsync。
按官方完整上下文上限的保守上界加最大输出预留，剩余额度不足一次请求则发送前停止；不把估算tokenizer当作硬计费界。
最多99个Task的1188次HTTP尝试仅为技术防线，不扩大token授权。价格未配置，因此费用为null。

首次启动在任何HTTP之前拒绝：发现遗漏登记 `stale_snapshot_has_no_current_risk_claim` 断言，实际请求0且尚未创建运行目录。
只补齐评测侧结构化断言与全分集元数据覆盖测试，未改任何问题、预期或Prompt。过旧快照不能产生当前覆盖天数或阈值结论，文本语义仍另审。
新预算/断言/独立oracle定向 **51 PASS**，4.12秒，Ruff与格式检查通过。
正式运行目录 `evaluations/results/diagnosis-holdout-live-20261020-01` 保留全部计划预期和逐次结果；任一正向自动失败或未知usage即停止，禁止删除失败重置预算。
本轮运行结论在下方追加；未完成独立语义审查前，业务质量率保持null、默认入口保持固定复盘。

### 真实轮次结果：停止，未放行

run_id `84d30a46-3338-4961-ad42-a5be61b7a27c`，实现指纹 `7ede34ed831603a9924a922cd842853b09b03397301bb20cf44a55d7b23859c6`。
原始回执 `evaluations/results/diagnosis-holdout-live-20261020-01`，对账/失败定位另存同级 `diagnosis-holdout-live-20261020-01-audit.json`，不改原回执。
计划20个问题各3次诊断与13个共同问题各3次固定基线，实际只完成前三个独立问题的18个Task：9固定、9诊断。
48次真实HTTP请求，全部有有效usage；输入/输出、SDK trace、Task账本与journal合计 **276,855 token**，未结算预留0。
发送前最大“已知+预留”上界1,319,064，小于授权3,000,000；未因token额度而停止。费用无配置，仍为null。

固定自动检查9/9、诊断8/9；仅为前三个问题的局部自动结果，不能作为保留集通过率或Agent收益。
H03第3次诊断失败：三次 `compare_periods` 取得两期支付件数、退款件数与退款金额，但没有程序绑定的退款占比事实。
模型提议 `target_covered`，图的完整性门禁将其改为 `evidence_gap`；报告已有数值经原始记录重算通过，退款问题的证据闭环未完成。
逐项自动失败为expected_outcome、required_capabilities、ratio_is_not_amount_change。系统按正向失败停止，后续17个独立问题未执行。
未自动重跑、筛选较好重复、调整Prompt或改写用例预期；保留集不能转成调参集。

另发现执行方式标注问题：评测注入真实ChatOpenAI时，报告被标为scripted_protocol。实际SDK HTTP trace及账本确认是真实模型调用；
原9份诊断报告与其hash保持原样，模式标签缺陷单列为证据限制，不重新包装成干净验收。
后续仅修正客户端分类与本地协议回归，不为此重启付费保留集。
业务质量率null、独立语义审查PENDING、release_eligible=false，默认入口继续为固定经营复盘。
后续如要再次正式放行，需先按新的独立开发样本验证问题，再建立未被用于调参的新保留集及明确的新轮次授权。

### 本地收尾验证与模式标签修复

新增整轮预算与断言后完整隔离PG16.2/HTTP **503 PASS**，无跳过，116.67秒。
之后仅修改 `app/diagnosis/graph.py` 的执行模式分类：显式 `diagnosis_protocol_stub` 才标协议模拟；
真实SDK用于已接纳的live任务标live_model，原demo任务仍不能被注入真实客户端变为付费任务。
新增使用真实ChatOpenAI SDK加本地MockTransport的离线HTTP回归，核对报告live标签及账本30token；它不是新的提供方请求。
图流程/预算/评测/oracle定向 **79 PASS**，11.11秒，覆盖此分类修复及原demo和脚本替身边界。
完整503是在这两个文件（graph及对应测试）变更前完成，补充回执列出hash差异，不能将79项称为第二次完整回归。
前端自17项Chrome与构建以来未再更改，因此保留原浏览器证据，不重复运行不相关页面。
最终补充回执 `evaluations/results/diagnosis-g6-g7-final-20261020-02/receipt.json` 保留503/79/51日志、正式失败审计与源码范围。
没有提交或推送；未改变模型Prompt、固定用例、预期答案、应用demo配置或默认固定复盘入口。

## 官方核对入口

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph 中断](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph StateGraph 源码](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/graph/state.py)

采用当前锁定版本提供的 API；检查点持久化不能替代调用账本的幂等与预算事务。

## 第九批：完整真实评测、续评对账与判定器纠错（2026-10-20）

用户明确G7不执行，本轮范围为G0—G6。对原§12.4逐字复核后，评测器不再因一般证据缺口或关键场景标签而提前终止整轮。
关键安全不变量失败、未知用量及全轮硬预算仍停止；每个完成度失败保留，并进入最终分母。
原18→59→80→99个Task分段续评，已执行项不重跑；完整祖先链与原3,000,000预算一致，最后为246请求、1,495,690 token，未结算0。
续评验证全部组/重复前缀、业务源/Prompt/问题/预期hash，拒绝业务行为变化拼接旧结果；通过排他持久承接记录防止双花额度。

修复评测器三类误拒：完整授权SKU子集的条件断言；商品名注入不应要求文档引用；Task依赖返回usage的阈值不得误写成最终绝对上限。
预算阈值按模型账本逐次核对，每次请求前旧累计须低于阈值，末次已知响应越线后不得再请求；全轮硬300万上限独立保护。
新增 `audit` 子命令对原回执零请求重算，逐项保留旧/新判定和原hash；`review --grading-audit` 在导入前再核对源码、原始回执和完整进度。
补验可编辑审查模板即使与审查文件同时删掉固定基线，也不能绕过“所有运行必须审查”。

完整PG16.2/HTTP后端 **528 PASS**，115.51秒；其后仅审查覆盖校验及对应测试变更，定向 **76 PASS**，4.84秒。
原17项浏览器及6项诊断真实API证据仍保留，本批未改变前端或业务实现/模型Prompt。
完整问题、39对共同比较、独立自动复核、失败定位、对账及限制见[28号记录](28-diagnosis-live-evaluation.md)。
固定流程39/39，诊断共同28/39、新增10/21、合计38/60（63.3%），未达到90%目标。默认继续固定Workflow。
这是由参与开发的Codex独立于被测DeepSeek进行的自动证据复核，不是人类业务人员验收或第三方审计。
项目尚未完成：仍需修正意图误判、否定约束、无关证据缺口及不稳定决策；未把G6运行完成偷换为全部功能通过。

## 第十批：意图、停止、范围事实与规则召回修复（2026-10-20）

按用户要求，每次先补修并完成本地/PG/浏览器验证，再恢复小批真实模型测试。
v3—v6分开保存契约和全部失败；新诊断为v6图/Prompt、schema2及注册表3，旧v1—v5保持恢复路由。
最新完整隔离PG16.2/HTTP606 PASS，随后仅预算承接与测试变更定向107 PASS，最终v6诊断Chrome6 PASS；前端构建及格式检查通过。
原40题59次及补充3题4次离线执行通过；真实样本、扣费祖先链和未完成正式门槛见[29号修复记录](29-diagnosis-v3-repair.md)。
这些是开发/回归证据，不替代新的20题各3次正式保留集评估，G7本轮仍不执行。

本批最终v7完整614 PASS、补验62 PASS、Chrome6 PASS；规则真实复测通过，310请求与1,941,277 token完整对账。正式新保留集96 Task未执行，等待另行预算授权；全部过程见29号记录与最终回执。
