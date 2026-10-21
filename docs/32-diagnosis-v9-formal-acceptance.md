# v9 新保留集正式复评与未放行结论（2026-10-21）

结论：96 Task 全部执行且逐条审查完成，自动契约与语义通过率均超过90%目标，但关键用例H59三次中两次以`evidence_gap`结束，
**关键门禁4/6（66.7%）未达100%**，`release_eligible=false`，G6不放行，默认入口仍为固定经营复盘。G7按用户指示排除。

## 授权、实现与计划

用户于2026-10-21明确：累计token上限提高到**6,000,000**并承接全部历史扣费；本轮96 Task完整执行、不截断、不复跑、不筛选；
若未达标先出结论，由用户决定是否继续修复。因此本轮不使用`--resume-from`，只用`--charge-from`承接3,350,505 token历史扣费，
不复制旧Task或旧通过率。

|项目|值|
|---|---|
|实现指纹|`78eddfe4e88a30371089c3401329b9a1c773377ee6fabbf8bb5248fac260b542`|
|问题文件SHA256|`84f65dc6d3cf73ea8af61727e7073685bfcbac62a48eac1af3d2cf9c69f62f06`|
|计划状态|`FROZEN_RUN_AUTHORIZED_20261021`（20题×3次诊断 + 12共同题×3次固定对照）|
|运行目录|`evaluations/results/diagnosis-v9-fresh-holdout-live-20261021-01/`|
|run_id|`baa2ee7a-523b-4d11-8364-52a7584cf48e`|

相对上一指纹`ef0d66ed…`，本轮唯一实现变更是`app/storage/repository.py`的一处Ruff格式化换行，语义未变；
题目、预期、Prompt、schema、注册表与`cases.json`均未改动。质量门禁先在同一最终源码上重跑本地回归，再发起付费请求：

|本地验证|结果|
|---|---|
|隔离PostgreSQL 16.2 / HTTP后端回归|652 PASS，0跳过，146.38秒|
|Ruff检查与格式检查|通过（修复了唯一一处格式不通过文件）|
|模型或图执行|本项验证为0；付费请求只在下一步正式轮发生|

## 完整执行结果

96个Task全部执行，`stopped=null`，无提前截断、无筛选、无重跑；每次运行使用独立新Task与临时SQLite，不复用旧缓存。

|组别|运行|独立题|自动及语义通过|说明|
|---|---:|---:|---:|---|
|固定流程对照|36|12|36/36，100%|与诊断同输入、同预算、同模型配置、同最终范围，配对校验通过|
|共同能力诊断|36|12|36/36，100%|12题全部三次正确|
|新增能力诊断|24|8|22/24，91.7%|8题中7题三次全对；H59两次失败|
|诊断合计|60|20|58/60，96.67%|—|

|正式指标（逐条审查后）|值|目标|
|---|---:|---:|
|行为/契约通过率|96.67%（58/60）|≥90%|
|正向任务完成率|96.30%（52/54）|≥90%|
|关键用例通过率|**66.67%（4/6）**|**100%**|
|关键事实/权限/版本/预算门禁|全部通过|全通过|
|配对输入、预算、模型、最终范围一致性|通过|全通过|
|放行|**false**|—|

p95延迟（nearest rank）：固定对照4.21秒、共同诊断3.50秒、新增诊断4.93秒。任务级硬边界未触发。

## 两条失败与实际缺陷

H59（规则查阅，关键用例，查询明确要求“列出不同阈值和原文；适用范围与版本优先级尚待核验”）：

- 第1、2次：逐字引用“低于七天”“低于十天”两份规则、引用存在性与“解读未核验”断言均通过，模型仍以`evidence_gap`结束，
  未把用户明确要求的原文查阅作为已完成目标交付；自动判定因`expected_outcome`失败，语义审查同判FAIL。
- 第3次：同样证据下以`target_covered`结束，全部断言通过，语义审查PASS。

两次失败不涉及金额、权限、引用或版本错误，而是**停止口径不稳定**：在同样已取得两份原文、引用与“解读未核验”断言全部通过的情况下，
模型两次把“适用范围与版本优先级尚待核验”当作剩余缺口并以`evidence_gap`结束，第三次仅因选择`target_covered`而全部通过。
v9继承的Prompt已写明“只要求查阅原文并标注待核验时可选`target_covered`”，但回执未保留该次模型的完整上下文，
**尚不能确认是缺口字段仍有残留，还是停止标签选择波动**；该定位不作为本轮结论。本轮不修改实现、不改题、不重跑，H59-1/2保持原始FAIL。

其余58次诊断无自动失败；语义审查另核对所有报告的结论边界、事实归属、因果措辞与规则适用性，未发现新增业务错误。
“利润不可计算”、澄清、多对象日序列、库存缺提前期与文档注入等考察点在各自3次内均按预期完成（题内通过率100%）。

## 逐条证据审查与审计

|项目|结果|
|---|---|
|0请求判定审计|`grading-audit.json`；原始回执未修改，新增模型请求0；审计与运行实现指纹一致|
|审查覆盖|96/96，模板、进度、回执SHA256逐一绑定，不能删除基线或失败记录|
|语义结论|94 PASS、2 FAIL（H59-1、H59-2）|
|审查执行者|AI代码作者，独立于受测DeepSeek，但**不是人工业务验收或第三方审计**；来源与限制见`review-provenance.json`|
|配对对照|36对全部有效，`paired_comparison_valid=true`|
|导入一致性|审查文件与审计文件哈希已写入`review-result.json`|

审查结论与本轮自动判定一致，没有把自动FAIL改成通过，也没有用旧版本结果替代本轮。
`review-result.json`的`reviewer_kind`沿用v7既有枚举`independent_external_reviewer`（schema仅允许`human`与该值），
其真实身份以`reviewer`文本和`review-provenance.json`为准，不得据此对外称为第三方审计。

## 模型用量与费用

|项目|本轮|累计|
|---|---:|---:|
|token|1,161,798|**4,512,303 / 6,000,000**|
|HTTP模型请求|184|707|
|未结算/未知用量|0|0，完整祖先链对账通过|
|费用|未知|未配置价格，不能写为0元|

每次请求前保守预占1,051,576 token，本轮结束时名义剩余1,487,697，足以覆盖一次同类预留；剩余额度未被使用。

## 与v7轮次的关系

v7正式轮原评分诊断52/60、按修正评分审计57/60，本轮58/60。两轮题包、Prompt与评分门槛版本不同，
不能据此宣称版本提升或Agent收益；共同题固定对照在两轮均为36/36。v7原始失败与本轮H59失败分别保留。

## 未完成事项

1. 定位并修复H59停止口径：先用现有开发/回归样本复现“查阅原文+标注待核验”的完成判定，确认`required_evidence_gaps`是否为空；
   还需要在回执中保留该次规划上下文，才能区分缺口误判与标签波动。修复后必须重建未用于调参的新题包，才能形成新的正式轮，
   不能把本轮改分或重跑为通过。
2. 人工业务验收与第三方审计仍未进行；题包为作者编制的合成变体，不是外部盲测，也不证明真实经营收益。
3. 目标环境HTTPS、容器代理链、真实数据接入与生产灾备未验证（G7按用户决定排除）。
4. `evaluations/results/`被Git忽略：本文记录摘要与指纹，原始回执、审查文件和快照保留在本地运行目录。

## 复现

```bash
# 免费：本地全量回归
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests

# 付费：需另行获得明确授权并设置累计上限
uv run --frozen python -m evaluations.diagnosis run \
  --cases evaluations/results/diagnosis-v9-fresh-holdout-plan-20261021-01/cases.json \
  --split holdout --repeats 3 --final-holdout --mode live \
  --provider DeepSeek --endpoint https://api.deepseek.com --model deepseek-flash \
  --api-key-env OPENAI_API_KEY --max-total-tokens 6000000 \
  --charge-from evaluations/results/diagnosis-v9-development-live-20261021-01 \
  --allow-paid --output evaluations/results/diagnosis-v9-fresh-holdout-live-<date>

# 0请求审计与审查导入
uv run --frozen python -m evaluations.diagnosis audit --run <运行目录> --output <运行目录>/grading-audit.json
uv run --frozen python -m evaluations.diagnosis review --run <运行目录> \
  --reviews <运行目录>/codex-evidence-reviews.json \
  --grading-audit <运行目录>/grading-audit.json --output <运行目录>/review-result.json
```
