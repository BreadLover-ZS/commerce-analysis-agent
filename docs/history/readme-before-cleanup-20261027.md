# 整理前入口快照：README.md

2026-10-27整理前原文，保留历史阶段与待办；只有相对链接按归档位置调整。当前状态请阅读[项目状态](../status.md)，不要把下文的旧“当前”表述作为最新结论。原始字节另保存在本次整理原始记录中。

---

# 电商运营分析助手 | commerce-analysis-agent

2026-10-27按用户授权启动U真实模型逐Task验证，第5位H01-3/fixed触发报告回退后立即停止，91/96位未执行。本轮新增10 HTTP／72,552 token，无新增UNKNOWN；累计已知11,122,121，旧冻结1,051,576，余额7,826,303。查明固定复核证据28,659字符超28,000后被整份排除，空证据仍进入复核；原自动5/5、执行时内容审查4/5保留，追加上下文审计后严格联合仅2/5。未付费重试，源码未改、缺陷尚未修复，G6未放行、G7排除；详见[61号停止与根因](../61-boundary-v2-U-formal-stop-and-root-cause-20261027.md)和[62号修复方案](../62-fixed-review-evidence-admission-repair-plan-20261027.md)。未提交推送，以下保留历史快照。

2026-10-27按58号方案完成边界语义与正式单Task入口修复：新增冻结边界策略2／answer-contract-v5及独立评分，旧任务继续旧契约；逐份审查、任意失败即停、保守预算承接、持久化恢复及同bundle配对已落地。最终1420项完整后端（含隔离PG）、19项Chrome、9种缺陷变异通过；267份旧评分及330份历史文件不变。U题包按schema5重新冻结，20题／96位全部未执行。本轮真实请求及付费新增0，剩余7,898,855 token不变；本地PASS，实际新包发送与正式保守承接待授权，旧UNKNOWN严格对账仍BLOCKED、G6未放行、G7排除，未提交推送。详见[59号本地验收](../59-boundary-and-formal-entry-local-validation-20261027.md)；以下保留历史快照。

2026-10-27完成下一轮U系列正式验证材料准备：20题／96个执行位，95项金额与件数义务、12项每日指标和11个贡献组经独立原始行复核；27种副本篡改及两个入口误启动均在凭据前拒绝。本轮新题Task／工具／模型执行0、付费新增0，业务与评测源码仍为6a628584…、254文件；余额7,898,855 token不变。材料本地PASS，但正式入口缺逐Task审查、所有失败即停及探针预算承接，另有未修复的边界同义判别缺口，正式付费准入BLOCKED、模型效果UNTESTED；G6未放行、G7排除，未提交推送。下一步先补正式入口、修复边界同义判别并完成本地验收，详见[57号准备报告](../57-next-formal-preparation-20261027.md)；以下保留历史快照。

2026-10-27完成2000万累计上限下的小批探针：七类、八次Task尝试，真实新增11 HTTP／94,907 token；保留H01初测FAIL，首错停后修复预算及工具能力声明，1337项完整后端、19项Chrome及13类缺陷变异通过，再按原两次HTTP上限复测PASS。累计已知11,049,569，历史UNKNOWN保守冻结1,051,576，有效占用12,101,145，剩余7,898,855；新增UNKNOWN0，原UNKNOWN及严格对账BLOCKED不变。两次零模型程序边界不计为模型效果；本轮是旧输入探针，G6未放行、G7排除，未提交推送。详见[56号结果](../56-small-model-probe-results-20261027.md)及[55号H01修复](../55-H01-probe-budget-root-cause-and-repair-20261026.md)；以下保留历史快照。

2026-10-26完成独立模型前预检，默认诊断v16/固定v6。初始5条反例复现失败，修复否定解释约束、最终报告重校验、正式入口冻结、证书链路及祖先题源去重；最终1285项完整后端（含隔离PG）、19项串行Chrome、32次旧Q回放和9种缺陷注入通过，267份旧评分及199份核心原件未变。代码本地PASS；实际历史UNKNOWN仍BLOCKED，余额距下一次保守预占差6238 token，新版本模型效果UNTESTED。本轮提供方调用0、额度不变，G6未放行、G7排除。修复与配套材料合为一个提交；验收回执保留当时的未发布快照，后续提交和推送以Git记录及远端SHA核对为准。详见[53号预检与教训](../53-pre-model-preflight-and-lessons-20261026.md)；以下保留历史快照。

2026-10-26按51号方案完成v16/固定v5本地修复：报告原子组合与容量保护、计量发送协议v2、双入口NOT_SENT及追加式对账验证器已落地。最终隔离PostgreSQL1059项、串行Chrome19项、5种缺陷注入通过；267份历史评分及199份核心文件未变。实际v15历史拒绝缺直接调用关联，仍BLOCKED且不生成证书；真实模型新增调用0，1200万额度不变、G6未放行、G7排除，未提交推送。详见[52号修复验收](../52-v16-repair-and-local-validation-20261026.md)；以下为历史快照。

2026-10-26 v16修复方案已形成：覆盖报告容量、未来发送前拒绝分类及历史UNKNOWN追加式安全对账；15项局部原型检查通过，35类修复验收反例已列明。业务修复尚未实施，原始账本/成绩不改，历史证据不足继续阻断；真实模型新增调用0、累计上限仍1200万。详见[51号修复方案](../51-v16-repair-plan-and-confidence-gates-20261026.md)。

2026-10-26 v15真实模型验证已停止并审查已有结果：计划96 Task，按原1200万累计预算在75份后发送前拒绝；自动与作者侧AI审查交集72/75。固定H03两份章节容量异常无报告，H13第三次日序列未答齐且暴露发送前预算拒绝误记Task UNKNOWN；21份H14—H20未执行。累计10,954,662/12,000,000 token、1576 HTTP；标准审计/扣费承接受阻，未修账、未增额、未额外付费重试。G6未放行、G7排除，详见[50号部分正式结果](../50-v15-formal-validation-budget-stop-20261026.md)；以下为历史快照。

2026-10-25按48号方案完成R1—R4本地修复：默认诊断v15/schema4/answer-contract-v4及固定复盘v4，安全提议纠错、独立答复完成度、金额见证和比较期支付金额直接展示已落地。925项完整PG/HTTP、19项Chrome、59次开发/回归及32次旧Q本地回放通过；v11/v14各96份评分未变。新T题包20题/96 Task已冻结且未执行，本轮真实模型请求0、额度不变；G6未放行、G7排除，未提交推送。详见[49号修复验收](../49-v15-repair-and-local-validation-20261025.md)。以下保留阶段快照。

2026-10-25 v14真实正式轮完成：用户授权1200万token后完整执行96 Task；原自动83/96、诊断47/60、正向41/54、固定自动36/36、关键6/6。作者侧严格审查91/96、交集80/96，G6未放行。新增153 HTTP/1,155,229 token，累计9,768,379/12,000,000，UNKNOWN0；完整账本与96份审查通过导入。两次真实漏答、工具路径/停止契约争议及固定展示缺口分别保留，未改评分或付费重跑；Q输入已执行，不能再当新保留集，G7排除。详见[47号验收](../47-v14-formal-acceptance-20261025.md)，材料未提交推送；以下为阶段快照。

2026-10-25代码预检完成：新增诊断v14/answer-contract-v3，修复对象排除、贡献维度、范围汇总义务、已保存模型响应恢复及正式启动预算保护；869项完整后端、18项Chrome、59次开发/回归及21条独立Worker回放通过，旧96份评分不变。本轮真实提供方请求0；新v14正式20题/96 Task仍未执行，旧v13冻结源码已失效。预算估计缺口1,004,521 token，未增额；G6未放行、G7排除。详见[46号预检结果](../46-pre-model-code-preflight-results-20261025.md)；本轮修复与配套材料合为一个提交，推送状态以Git及远端SHA核验为准；以下保留阶段快照。

2026-10-25正式轮准备：v13新20题／96 Task题包已冻结，含64项逐对象指标、12项每日事实及11个贡献组；29项冻结测试、15项元数据反例和零请求作者预检通过。题包尚未执行，本轮付费新增0，应用源码及旧题包/评分/账本不变。预算估计累计11,004,521，高于现有1,000万上限1,004,521，未增额；G6未放行、G7排除。材料与边界见[44号准备记录](../44-v13-formal-packet-preparation-20261025.md)，本轮尚未提交推送。

2026-10-23本轮修复：按[42号方案](../42-v13-request-obligation-repair-plan.md)完成41号A01—A04，新增诊断v13/schema3/注册表6；对象排除、逐商品金额/件数、贡献维度与完整缺口均已接入。13条独立Worker回放、59个离线Task、18项Chrome及原始行算术复验通过；最终786项PG/HTTP通过，详见[43号修复验收](../43-v13-request-obligation-repair-and-validation.md)。旧v11正式96份评分逐项不变，本轮付费新增0，累计8,613,150/10,000,000 token。v12旧冻结指纹已失效，v13正式模型效果仍未验证，G6未放行、G7排除。本轮修复与验收材料按用户要求纳入同一提交，推送结果以Git记录与远端SHA核验为准；以下按阶段保留历史记录。

2026-10-23修复前深度验收：历史提交4af0181的755项PG/HTTP、17项Chrome及构建通过，但新增反例复现3类P1（排除对象选错、逐商品义务漏登、贡献维度未校验）及1项P2缺口截断。**G0—G6整体验收不通过**；v12真实模型正式轮未执行，本轮付费新增0，业务代码未改、G7排除。详见[41号验收报告](../41-v12-deep-local-acceptance.md)。

2026-10-23前轮v12修复快照：按39号结果新增诊断v12（schema2/注册表5），修复局部不适用误作全局缺口、字面比较强制动态核查，并补充批量能力/剩余额度与紧凑上下文。
最终755项PG/HTTP、59个离线Task及6项Chrome均通过；新20题/96 Task计划已冻结，详见[40号修复记录](../40-v12-planning-completion-repair-and-validation.md)。本轮付费新增0，真实模型v12尚未运行；G6仍未放行，G7排除。

2026-10-23最新正式结果：v11完整96 Task已执行、审计及逐份作者侧AI审查。诊断53/60、正向47/54、固定36/36、关键6/6，G6仍未放行。
累计8,613,150/10,000,000 token、1288次HTTP、未知0；H01漏答、H05停止口径及H08报告门禁失败保留，详见[39号验收](../39-v11-formal-acceptance.md)。下文各批次记录为其阶段快照。

2026-10-23执行前历史修复快照：按37号方案完成诊断v11/注册表5、固定答复v3的必答项、上下文、建议前提及自动收尾修复，旧版本继续恢复。
728项PG/HTTP、59 Task离线及6项Chrome通过；新20题/96 Task计划已冻结，提供方新增请求0；当前真实模型正式结果仍为v10未放行。验证与剩余出口见[38号记录](../38-v11-request-contract-repair-and-validation.md)。

产品名：**电商运营分析助手**；项目目录/仓库名：`commerce-analysis-agent`。
完整介绍名：**电商运营分析与竞品研究 Agent 系统**。
命名与迁移记录见 [项目命名](../10-project-naming.md)。

最新正式轮（2026-10-22）：用户已批准累计900万token，v10完整96 Task已执行、审计及逐份作者侧AI审查完成。
自动92/96，诊断共同通过54/60、正向48/54（88.89%）、固定27/36、关键6/6，**G6仍未放行**。
库存快照误述未重现，但存在否定词误拒答、逐商品/两期净额漏答及固定建议冲突，见[36号正式验收](../36-v10-formal-acceptance.md)。
累计7,273,355/9,000,000 token、1123次请求、未知0。审计链深度补修定向74 PASS，执行前验证694项PG/HTTP、59次离线、6项Chrome见[35号记录](../35-v10-repair-and-validation.md)。
H61—H80旧正式轮结果和8条语义FAIL完整保留在[34号正式验收](../34-diagnosis-h61-h80-formal-acceptance.md)。
下文各批次验证数字按其时间和源码版本保留，不能合并为当前生产验收。

结合销售、退款、库存、内部规则和公开竞品资料，生成区分事实、分析与建议的报告。
关键结论关联原文或确定性指标；长任务可以复核、取消、恢复，并交付 Markdown/PDF。

基于 [didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents) 二次开发，
来源与实际贡献见 [PROVENANCE](../../PROVENANCE.md)。定位为可复现学习、求职展示及逐步走向小规模试用。
当前默认主入口为**经营复盘（固定流程）**：生成模拟场景或导入CSV，确认不可变数据版本，
检查质量并生成可核对报告。程序计算事实与变化观察，AI选择受控假设并提出核对建议。
使用、数据契约、开发与验收边界见[经营复盘开发与验证](../21-commerce-review-development-validation.md)。
本轮251条后端、9条新离线、原32条离线和11条Chrome检查通过；
真实模型九场景及两个场景各两次重复经过自动检查与逐条证据核对。
模拟数据结果不代表真实业务效果，Docker/目标HTTPS仍待验；详见21号文档。
以下214条等是此前审计批次记录。
2026-10-16已修复恢复快照、库存精度、附件检索、文档配额、代理信任配置及前端证据读取竞态，
本地214条后端、32条离线评估与9条Chrome浏览器检查通过，范围见[审计修复与验证](../20-audit-fixes-validation.md)。
此前纯Python交付与负载见[历史交付验收](../18-python-delivery-validation.md)。
f3a12de的库存、内部规则、综合补货三个真实教学单例见[模型记录](../16-usage-receipts-and-provider-check.md)，
它们是原实现的历史证据，本轮没有新增真实模型复测；不代表新实现模型效果、稳定通过率或生产验收。

浅色工作台的界面、交互与本地验证见[前端重构记录](../22-frontend-redesign.md)。

2026-10-19 起按 [诊断 v1.1 契约](../commerce-diagnosis-spec-v1.1.md) 实施诊断开发；用户已于2026-10-20明确本轮G7不执行，当前范围为G0—G6：
销售诊断、库存/规则工具、版本路由、同输入对照与维护工具已落地。当前新诊断使用v15图/Prompt、schema4/注册表6，固定复盘使用答复v4，保留旧诊断v1—v14及固定v1—v3业务契约；以下v9成绩属于历史版本。
最新v9完整本机隔离 PostgreSQL/HTTP 后端回归656 PASS（H59修复后）；诊断Chrome6 PASS，62次离线执行通过；v9多商品日序列开发题3次真实验证通过。
40个开发/回归问题的59次离线执行及补充3题4次执行均通过，提供方请求0。
另完成PG双Worker的20任务教学负载及新旧等待任务备份/恢复演练，三页样例PDF已渲染核验。
2026-10-21 v9新保留集正式轮96 Task已完整执行并逐条审查：固定对照36/36、共同诊断36/36、新增诊断22/24，
行为率96.67%（58/60）、正向完成率96.30%，关键用例4/6未达100%，**G6未放行**；累计4,749,932/6,000,000 token、743次请求。
失败为H59两次停止口径不一致，已修复并用R61—R66新题包18 Task复评18/18通过（全部`target_covered`），仍待完整20题新正式轮重判，见[v9正式验收](../32-diagnosis-v9-formal-acceptance.md)。
当前进度与未完成事项见[2026-10-21进度整理](../31-development-progress-20261021.md)，历史过程见[诊断开发记录](../23-diagnosis-development.md)。
2026-10-22已补充零请求题包冻结/预检与正式运行`--frozen-plan`保护，全量隔离PG/HTTP回归679 PASS。
用户已授权累计800万token，[H61—H80完整96 Task](../acceptance/diagnosis-v9-h61-h80/README.md)已执行并逐份审查：
自动96/96、诊断语义59/60、固定29/36、关键6/6，但库存快照缺失误述触发语义事实门槛，**G6仍未放行**。
累计5,990,281 token、934次请求、未知0；完整失败与判定见[本轮正式验收](../34-diagnosis-h61-h80-formal-acceptance.md)，
准备与验证见[复现交付](../33-diagnosis-acceptance-preparation-20261022.md)。
新调用链与个人练习见[诊断源码路线](../24-diagnosis-source-map.md)。[评测入口](../26-diagnosis-evaluation.md)与[运行维护](../27-diagnosis-operations.md)已交付。
本轮另补全轮token硬预算；完整保留集已累计执行99个Task、246次请求、1,495,690 token。独立自动证据复核诊断38/60，未达90%目标，继续保留固定Workflow。
失败、续评与实现边界见[完整真实评测](../28-diagnosis-live-evaluation.md)；本轮不执行G7。
本批迭代修复在本地验证后恢复真实开发测试，历史扣费继续计入累计预算。用户已将累计上限提高到500万token，新的96 Task正式复评已完成，随后按失败记录继续v8/v9修复；最新结果见[修复与复验](../29-diagnosis-v3-repair.md)。
诊断界面见[规则与证据视图](../images/diagnosis-rules.png)、[移动端报告](../images/diagnosis-mobile.png)。
当前输入的覆盖核对见[移动端预览](../images/diagnosis-preflight-mobile.png)，逐项未完成需求见[验收索引](../25-diagnosis-acceptance-matrix.md)。

![电商运营分析助手工作台](../images/workbench-report.png)

## 主要能力

- LangGraph 组织计划、资料获取、草稿、校验、复核和交付；复杂研究使用 Deep Agents。
- 固定资料流程、单 Agent、专家分工三种策略可选；关闭默认通用子 Agent，支持受控对比。
- 经营指标由受限业务工具计算，身份来自服务端，模型不能执行任意 SQL。
- Skills、结构化输出、上下文隔离、持久化检查点与人工 interrupt/resume。
- 商品/单位/周期/快照与指标绑定，关键事实句由程序渲染；研究发现保留逐字引用与冲突。
- 同任务、同工具与周期复用首次可用指标快照；来源使用结构化文档/片段身份与显式排除原因。
- 来源/片段/指标/内容哈希独立保存，证据选择清单、失败修正与明确降级。
- PostgreSQL 任务队列、租约与晚到结果校验、幂等提交、取消、事件游标重放。
- Cookie 登录、CSRF、资源归属、公开关键词边界、受限附件解析。
- React 工作台、PDF 导出、固定评估集、真实 SDK 协议测试、故障注入和 CI。
- SQLite/PostgreSQL离线备份恢复、可信备份校验与隔离HTTP教学负载入口。

技术：Python3.12、Deep Agents0.7.25、LangChain1.4.5、LangGraph1.2.15、FastAPI、
SQLAlchemy/Alembic、PostgreSQL16、React19、TypeScript、Vite、Playwright。依赖由 uv/pnpm 锁定。

## 五分钟本地启动

需要 Python3.12、uv、Node22+、pnpm11.25.0。没有模型密钥也能跑明确标识的 demo：
以下命令均从 `commerce-analysis-agent` 项目目录开始。

```bash
cp .env.example .env
uv sync --frozen --python 3.12
uv run python scripts/dev_backend.py
```

另一个终端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

打开 [本地工作台](http://localhost:5173)，登录 `demo@example.com` / `demo-commerce-2026`。
默认业务数据覆盖2026-08-02至09-30，库存快照为09-30；竞品是模拟资料。
默认先选择模拟场景，生成预览并“确认使用此数据版本”，查看质量检查后开始复盘。
点击“依据”核对数字，勾选草稿复核可体验暂停与恢复。库存和竞品在“库存与竞品研究（高级）”。

**demo 未调用模型或真实搜索；live 需要自己配置兼容模型与外部服务。**
真实联调及评估步骤见 [评估文档](../04-evaluation.md)。

## PostgreSQL 容器版

```bash
cp .env.example .env
# 保持默认值只用于本地演示；正式部署前设置独立凭据。
docker compose --env-file .env -f docker/docker-compose.yaml up -d --build
```

打开 [容器工作台](http://localhost:8080)。数据库和文件卷持久化，API/Worker 分离。
容器、HTTPS、账户和数据导入说明见 [部署文档](../05-deployment.md)。

## 阅读顺序

学习只需从 [学习总览](../learning/01-总览.md) 开始，依次阅读 **总览 → 后端主链 → Agent与证据 → 实践与自测**，共 4 篇。六周安排和面试题已合入，不需要另外走一套路线。

其他文档按需查阅：

- **查设计和源码：** [架构与边界](../01-architecture.md)、[源码调用链速查](../02-code-walkthrough.md)。
- **使用和部署：** [经营复盘数据与验证](../21-commerce-review-development-validation.md)、[评估与真实联调](../04-evaluation.md)、[启动和部署](../05-deployment.md)。
- **核对历史证据：** [审计修复验证](../20-audit-fixes-validation.md)、[历史交付验收](../18-python-delivery-validation.md)、[模型验证](../16-usage-receipts-and-provider-check.md)。这些记录不需要逐篇学习。
- **开发与发布：** [第二轮开发文档](../08-development-plan.md)、[上传 GitHub 与成果整理](../07-publishing.md)。

## 项目结构

```text
app/
  core/          配置、认证、文件边界与解析
  commerce/      经营计算、词法检索、样例与数据导入
  storage/       业务表、任务、租约、证据和事件
  research/      Agent、Gateway、LangGraph、Schema及校验
  skills/        经营复盘与竞品研究流程
  tasks/         独立 Worker 与检查点连接
  api/           登录、任务、附件、事件、依据与下载
frontend/        工作台和浏览器验收
migrations/      Alembic 迁移
data/           明确标注的经营/竞品/文档样例
tests/          业务、安全、SDK协议、恢复、PostgreSQL测试
evaluations/    历史32条用例、诊断60例分集、独立数值oracle与配对评测器
docker/         API/Worker/PostgreSQL/前端/HTTPS配置
docs/           架构、学习、部署、评估与验收
```

历史 examples/ 与部分知识库/图片保留用于上游对照，不属于新主应用的启动或验收入口。
完整历史复现需要使用其原始依赖环境，见 [示例说明](../../examples/README.md)。

## 验证

```bash
uv run ruff check app tests migrations evaluations scripts
uv run ruff format --check app tests migrations evaluations scripts
uv run pytest -q
uv run python -m evaluations.commerce_review --mode demo --output evaluations/results/my-review-demo
uv run python -m evaluations.run --mode demo
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
cd frontend
pnpm build
pnpm format:check
pnpm exec playwright install chromium
pnpm test:e2e
```

未提供 TEST_POSTGRES_URL 时 pytest 显式跳过 PostgreSQL 用例；临时 pgserver 或 CI 可运行。
脚本模型测试证明 SDK 协议连通性，离线32条用例证明固定业务契约；真实模型质量、真实
搜索/RAGFlow 与公网运行应分别验证，不从这些测试中推导生产成功率或性能。
