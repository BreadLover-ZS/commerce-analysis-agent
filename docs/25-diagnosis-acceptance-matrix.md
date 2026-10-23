# 诊断 v1.1 需求与验收证据索引

2026-10-23当前修复：按39号结果新增诊断v12（schema2/注册表5），修复局部不适用误作全局缺口、字面比较强制动态核查，并补充批量能力/剩余额度与紧凑上下文。
最终755项PG/HTTP、59个离线Task及6项Chrome均通过；新20题/96 Task计划已冻结，详见[40号修复记录](40-v12-planning-completion-repair-and-validation.md)。本轮付费新增0，真实模型v12尚未运行；G6仍未放行，G7排除。

2026-10-23最新正式结果：v11完整96 Task已执行、审计及逐份作者侧AI审查。诊断53/60、正向47/54、固定36/36、关键6/6，G6仍未放行。
累计8,613,150/10,000,000 token、1288次HTTP、未知0；H01漏答、H05停止口径及H08报告门禁失败保留，详见[39号验收](39-v11-formal-acceptance.md)。下文各批次记录为其阶段快照。

2026-10-23执行前历史修复快照：按37号方案完成诊断v11/注册表5、固定答复v3的必答项、上下文、建议前提及自动收尾修复，旧版本继续恢复。
728项PG/HTTP、59 Task离线及6项Chrome通过；新20题/96 Task计划已冻结，提供方新增请求0；当前真实模型正式结果仍为v10未放行。验证与剩余出口见[38号记录](38-v11-request-contract-repair-and-validation.md)。

2026-10-22最新结果：v10完整96 Task已执行、审计与逐份作者侧AI审查，诊断54/60、固定27/36、关键6/6；正向48/54未达90%，G6仍未放行。
累计7,273,355/9,000,000 token、1123次请求、未知0；审计器17轮历史链兼容补修定向74 PASS，原评分无变化。
具体失败、执行/审计指纹及后续缺口见[36号正式验收](36-v10-formal-acceptance.md)。以下为各批次历史快照。

原文覆盖G0—G7/GV01—GV30；2026-10-20用户明确本轮G7不执行。运维相关证据保留，其未验部分不作为本轮G0—G6开发门槛。局部通过仍不能替代对应范围的完整交付。
2026-10-20 建立；后续应追加当前版本的运行记录，而不将历史绿色移植到新版本。

2026-10-22最新修复：v10/schema2/注册表4及固定报告v2已实现，694项隔离PG/HTTP、59次离线执行、6项Chrome通过；
旧正式轮失败不回写，新v10正式模型轮尚未运行，G6继续未放行，详见[35号修复记录](35-v10-repair-and-validation.md)。

2026-10-22当前状态：同指纹隔离PG/HTTP回归679 PASS；H61—H80完整96 Task已执行审查，自动96/96、
诊断语义59/60、固定29/36、标记关键6/6，但H77-2停止说明错误声称缺库存快照，语义事实门槛失败，G6仍未关闭。
累计5,990,281/8,000,000 token、934次HTTP、未知0；完整结果及8条语义FAIL见[34号正式验收](34-diagnosis-h61-h80-formal-acceptance.md)。

2026-10-21历史状态：v9正式保留集96 Task已完整执行并逐条审查。固定对照36/36、共同诊断36/36、新增诊断22/24；
行为率96.67%（58/60）、正向完成率96.30%（52/54），但关键用例H59两次以`evidence_gap`结束，关键率4/6，
`release_eligible=false`，**G6尚未关闭**。累计4,749,932/6,000,000 token、743次请求，未知用量0。
v7正式轮经等价路径评分修正后57/60（95%），原52/60记录保留；两轮题包与门槛不同，不能相互替代。
未被用于调参的新题包尚未建立完整版本；H59停止口径已修复，并以R61—R66新题包18 Task复评18/18通过（停止原因全部`target_covered`），
该结果仅为修复证据，G6仍需完整20题新正式轮才能重判。
完整结果见[32号正式验收](32-diagnosis-v9-formal-acceptance.md)，
阶段、预算和边界见[31号进度整理](31-development-progress-20261021.md)。下文保留各阶段历史证据。

状态说明：PASS_LOCAL 指列出的本机/隔离环境断言通过；PARTIAL 指需求仍有未实现或未验证部分；
UNTESTED 指当前没有足以验证该项的运行证据。所有 PASS_LOCAL 都不表示生产验收。

当前本地回归回执：`evaluations/results/diagnosis-v9-acceptance-prep-20261022-01/receipt.json`（679 PASS，当前指纹）。
以下为历史本地汇总回执：`evaluations/results/diagnosis-g6-g7-local-20261020-01/receipt.json`。
保留491项完整隔离PG/HTTP后端、17项Chrome（6项诊断真实API）、40个独立开发/回归问题的59次demo执行、
PG16.2双Worker/并发4的20任务教学负载、失败历史与移动端对照截图。
491项对应前端对照前的后端版本；完整17项与构建覆盖最终对照界面，汇总回执按文件hash区分范围。
随后新增token硬预算和断言登记，完整隔离PG/HTTP **503 PASS**（116.67秒）；再修正真实SDK执行方式标注，相关 **79 PASS**（11.11秒）。
最新补充回执：`evaluations/results/diagnosis-g6-g7-final-20261020-02/receipt.json`，按源码hash区分503项与标注修复的验证范围。
真实模型保留集另留独立回执，不计算尚未独立审查的业务通过率。

## 三十个关键用例

| 用例 | 当前证据 | 判断与尚缺部分 |
| --- | --- | --- |
| GV01 筛选、缓存、版本 | `test_diagnosis_tools.py::test_gv01_gv21_filters_topk_and_normalized_query_identity` | PASS_LOCAL；SKU/品类与规范过滤、TopK分别核验 |
| GV02 抵消、其他与总体对账 | `test_gv02_independent_offset_contributions_and_200_percent`；独立整数分样本 | PASS_LOCAL；200%贡献允许，保留正负项 |
| GV03 退款占比与金额分离 | `test_gv03_ratio_up_when_refund_amount_and_count_fall` | PASS_LOCAL；不把分母效应当成金额增加 |
| GV04 缺数、零、非正基期、利润缺口 | `test_gv04_missing_and_explicit_zero_remain_different`、`test_gv04_nonpositive_base_does_not_produce_change_rate`、图零变化测试 | PASS_LOCAL；真实模型任务完成率随G6验证 |
| GV05 不可变输入与跨用户来源 | `test_gv05_gv26_owner_sources_scope_and_cache_reauthorization`、来源/PG来源组、内容篡改测试 | PASS_LOCAL；无新增任意来源入口 |
| GV06 非法工具/参数/周期 | `test_gv06_rejected_arguments_never_execute_or_consume`、图invalid loop及v2代码工具拒绝测试 | PASS_LOCAL；脚本提议，不是模型能力评分 |
| GV07 注入与权限 | manifest注入样本、`test_v2_rejects_injected_code_tool_with_fixed_budget`、库存/规则真实API浏览器路径 | PASS_LOCAL代码权限边界；真实模型注入表现未测 |
| GV08 等价工具路径 | 分支图测试、`diagnosis_grading.py`能力/结果判定及独立oracle | PASS_LOCAL；不限制唯一工具顺序，业务语义另审 |
| GV09 重复规格、无信息停止 | 图cached loop与TopK伪新信息测试 | PASS_LOCAL；旧信息不能凭新步骤ID续命 |
| GV10 超时/硬杀未知用量 | 模型账本GV10及SQLite/PG发送前、落库前SIGKILL | PASS_LOCAL；UNKNOWN保留预占并停止新模型请求 |
| GV11 工具提交与checkpoint错位 | 销售及库存提交后真实SIGKILL、PG接管复用 | PASS_LOCAL；保留原call/evidence，不重新扣有效调用 |
| GV12 旧worker写入拒绝 | 工具/模型/步骤/发现/报告/产物fencing测试与PG组 | PASS_LOCAL |
| GV13 重复/过期/代答 | 澄清CAS/TTL/所有权、PG重复答复与唯一领取、v1/v2复核API | PASS_LOCAL；浏览器刷新继续使用持久请求 |
| GV14 取消与在途调用 | `test_gv14_cancellation_keeps_inflight_usage_but_stops_new_calls`、工具取消、孤立产物不可见、浏览器等待取消 | PASS_LOCAL；不承诺供应商物理撤回已在途请求 |
| GV15 旧图/等待/重启恢复 | 旧v1 schema/prompt golden、SQLite/PG SIGKILL、v2库存澄清、未知graph拒绝 | PASS_LOCAL恢复边界；另有PG停服D3→head等待任务迁移；目标滚动升级未测 |
| GV16 规则冲突/库存过旧/缺SKU | 来源纯算法、v2未核验阈值候选、未来/过旧与缺SKU测试 | PASS_LOCAL；不把冲突候选标成已验证语义 |
| GV17 伪造数字/引用 | v1/v2报告内容篡改、原始源重算、快照/原文/解读伪造门禁 | PASS_LOCAL；门禁不依赖模型投票 |
| GV18 刷新/事件恢复 | 新诊断真实API浏览器刷新、澄清后复核、反馈、追问；原事件权限/游标测试 | PASS_LOCAL轮询链；前端没有新增SSE，不声称完成SSE重连 |
| GV19 公平Workflow/Agent对照 | 同输入适配、G6配对CLI与40例59次开发/回归demo，对照界面两项真实API浏览器测试 | PASS_LOCAL自动边界；真实20×3与独立业务审查另记，不能计算Agent提升 |
| GV20 一致备份恢复 | `test_diagnosis_operations.py`、`test_diagnosis_operations_postgres.py`协调备份恢复 | PASS_LOCAL；新旧完成/等待/取消/UNKNOWN、checkpoint/产物/来源hash均核对；目标环境未测 |
| GV21 旧metric_scope不误合并 | GV01/GV21筛选缓存、独立ToolQueryResult/Evidence | PASS_LOCAL |
| GV22 显式两期与启用边界 | 非相邻手算、重叠/不等长拒绝、商品启用与跨期退款；组合质量预览API与真实浏览器测试 | PASS_LOCAL；预览按实际两期显示缺行，改范围后重新确认 |
| GV23 已落库模型输出复用 | `test_gv23_output_and_usage_reused_after_checkpoint_gap`及PG模型输出提交后SIGKILL | PASS_LOCAL；无第二次真实SDK请求 |
| GV24 STARTED发送前/后杀进程 | `test_gv24_started_without_committed_response_never_retries`及PG两个真实窗口 | PASS_LOCAL；不猜测免费重试或返还额度 |
| GV25 中断错位/旧批准 | 两种输入/检查点SIGKILL、旧hash批准拒绝、v2版本复核浏览器 | PASS_LOCAL |
| GV26 来源交集与撤权缓存 | bundle含文档但只选sales、同租户所有者隔离、缓存读取前复验 | PASS_LOCAL |
| GV27 全局预算与冗余探索 | 8步/10工具/12模型、保留报告额度、冻结限额、无信息/缓存循环、请求前预算拒绝 | PASS_LOCAL硬边界；正式任务行为率仍待G6 |
| GV28 库存代理不可计算 | 零支付/缺行/新品不足完整窗口/缺lead/退款回库未知；独立库存手算与整数比较 | PASS_LOCAL；不是实际出库或安全库存承诺 |
| GV29 临时文件、发布、清理 | 发布隔离测试、`cleanup_artifacts.py`与operations回归 | PASS_LOCAL；默认dry-run，停服apply、24小时下界、登记文件/活动任务/符号链接保护 |
| GV30 迁移与回滚并存 | 旧数据迁移/破坏性降级拒绝、PG等待任务D3→head及新v2准入、同批备份恢复 | PASS_LOCAL维护升级边界；目标滚动发布与生产灾备未测 |

上述短测试名均位于 `tests/test_diagnosis_*.py`，PG包装位于
`test_diagnosis_postgres.py`、`test_diagnosis_runtime_postgres.py`、`test_diagnosis_sources_postgres.py`。
精确运行结果与失败历史以23号开发记录及对应回执为准，不按测试函数命名推断业务效果。

## 阶段剩余出口

- G0：60例及20/20/20分集、独立预期和目标表已固定；真实延迟/成本阈值不能由教学负载填成业务SLA。
- G1—G4：本地/PG工具、图、账本、来源和恢复已验证，真实模型业务表现须单独判断。
- G5：组合预览与同输入对照已实现，移动端/刷新/澄清范围不同比较经过真实API浏览器回归；本人练习待本人完成。
- G6：CLI、断言、harness、全面指纹、分组配对及审查导入已实现；真实保留集和独立审查单列，不能用demo替代。
- G7：新诊断备份/恢复、清理、维护升级、探针与双Worker教学负载已有本机运行证据；目标部署和业务核对仍待外部环境。

## 外部证据边界

用户已明确沿用项目DeepSeek配置，并授权整轮累计最多300万token。评测进程仅读取指定配置中的模型字段与密钥，应用仍为demo。
20条保留集各3次及13条共同基线各3次，最多99个Task；本轮实际18个Task、48次HTTP、276,855 token，H03第3次诊断证据缺口后停止，未放行；正式结果见23号开发记录。
不能重置全轮额度、筛选重复、用保留集调Prompt或把自动判定当独立业务审查。
目标HTTPS/容器代理链、真实经营反馈与本人练习仍为UNTESTED。

## 2026-10-20完整真实评测补充

G7按用户指示不执行。G6原20×3诊断及39个固定基线共99 Task已完整保留，246请求、1,495,690 token，全部已知用量。
修正判定与独立自动证据复核后固定39/39、共同诊断28/39、新增诊断10/21，诊断合计38/60，release_eligible=false。
完整后端528 PASS，随后审查模板覆盖校验定向76 PASS；原始失败、所有续评与纯离线审计均保留，详见28号文档。
GV08/GV19的评测器已可执行，不意味着模型路径与任务完成率达标；G2/G6仍有真实任务完成度缺口。

## v6本地修复补证（2026-10-20）

完整隔离PG16.2/HTTP606 PASS（136.71秒、无跳过），随后预算承接与测试3文件变更定向107 PASS（6.40秒），最终诊断Chrome6 PASS（33.7秒）。
新增证据覆盖否定约束、规则对象与计算目标区分、同manifest引用、日序列全范围、有界停止纠正、澄清后净额事实和新旧注册表缓存/原文重算。
原开发/回归40题59次及补充3题4次离线执行通过，提供方请求0。真实失败与各版本结果分别保留，见[29号记录](29-diagnosis-v3-repair.md)。
G6正式20题各3次新保留集尚未执行；不得将这些PASS_LOCAL或小批开发实测升级为业务放行。G7本轮不执行。

最终v7补证：完整614 PASS（138.86秒、无跳过），随后2文件目标缩小补验62 PASS（24.57秒），诊断Chrome6 PASS（30.6秒）。规则真实复测原文/引用/未核验标记/完成标签通过；累计310请求、1,941,277 token全部对账。仍仅开发样本，新的96 Task正式轮次尚未执行，G6保持未放行。

更新：用户已明确授权累计500万token（承接历史1,941,277 token），新的96 Task正式复评已启动。旧原始失败不删除；本轮结束并逐项核对前G6仍未放行。
