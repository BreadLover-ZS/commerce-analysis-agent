# v13正式题包（2026-10-25，未执行）

已冻结20题／96 Task计划：60个诊断、36个共同能力固定对照。**Task/工具/图/模型执行0，付费新增0，G6未放行。** 范围、原始行预期及预算见[44号准备记录](../../44-v13-formal-packet-preparation-20261025.md)。

|文件|用途|
|---|---|
|`cases.json`|20题原始业务数据、授权范围、原Expected格式；正向题只接受target_covered|
|`semantic-contract.json`|逐题源范围及断言、完整作者义务、v13版本要求；整体hash进入manifest|
|`answer-obligations.json`|作者义务可读副本；64项逐对象指标、12项每日事实、11个贡献组及原始行预期|
|`provenance.json`|旧题族来源、新SKU/日期/数值变体、已执行数据去重记录及限制|
|`manifest.json`|冻结实现、题包、语义契约、目标、预期、预算祖先及96 Task计划|
|`prepare.py`|仅生成数据/作者预期，已有包时拒绝覆盖|
|`verify.py`|核心冻结预检＋独立整数行/范围/单位/日期/维度/义务核验；不执行应用|
|`check_tampering.py`|1个合法副本＋14个元数据篡改反例，不执行Task、工具或模型|
|`preflight-20261025.json`|最终零调用预检结果；预算不足和未授权状态保持|
|`tamper-controls.json`|15项副本核验回执|
|`preparation-receipt.json` / `SHA256SUMS`|源码/旧文件保护/本轮测试及材料hash|

主要修复覆盖：H01为3 SKU的27项金额及SKU贡献；H04要求SKU贡献，H09要求品类贡献；H14排除P141、仅答P142及其净额/件数；H15要求两SKU的12项支付/退款件数。H13含两SKU两期12项每日事实和4项汇总。
H01等题若出现未完成项，必须完整保留并披露省略。题包不注入低预算，不声称已经运行或必然出现27条缺口；强制预算反例继续保留在43号证据中。

对象排除题属于新增能力组；不与全授权范围的固定复盘直接比较总额。授权范围预期与实际所问子集预期分开记录，均由整数原始行核对，不能互换。
本包是作者依据旧合成题族生成的新数据变体，不是外部业务盲测；1,398份已执行输入无精确数据复用，不证明语义独立。题号必须结合本包hash、实现版本和未来run_id识别，不能沿用旧同号题成绩。

零请求核验（仓库根目录）：

```bash
.venv/bin/python -m evaluations.diagnosis_plan check \
  --directory docs/acceptance/diagnosis-v13-h01-h20
.venv/bin/python docs/acceptance/diagnosis-v13-h01-h20/verify.py
.venv/bin/python docs/acceptance/diagnosis-v13-h01-h20/check_tampering.py
```

上述命令只读正式包，反例使用临时副本，不加载密钥或发送模型请求。准备脚本在已有包中拒绝运行；如需重新编制，应在新的同级题包目录生成并审查，不修改本包。

冻结实现：`c1e66bde7a33b2b4970f9ef79b1e239ff279a0ce82e70ae48f98e8b3685f3716`。
累计8,613,150/10,000,000 token、1288次提供方HTTP、未知0。名义余额1,386,850；按上一96 Task轮＋单请求保守预占估计累计11,004,521，超过现有上限1,004,521。
manifest没有增加额度；`authorization_required=true`、`proposed_limit_estimated_sufficient=false`、`release_eligible=false`。预检PASS不构成正式轮执行授权，本目录不提供付费执行命令。
