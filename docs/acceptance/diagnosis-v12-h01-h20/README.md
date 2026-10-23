# v12新正式题包（2026-10-23，未执行）

20题各3次诊断，12道共同题各3次固定对照，共96 Task。新题包 Worker/图/模型执行0，冻结与预检均为零提供方请求。
待单独正式轮授权；预算估算不足状态原样保留。历史v11成绩及失败不覆盖，G6仍未放行。

|材料|用途|
|---|---|
|[cases.json](cases.json)|新的业务数据、独立原始行预期、必需断言|
|[semantic-contract.json](semantic-contract.json)|逐题SKU数量、源范围与断言；H13明确每SKU日序列且至少2个SKU，预检绑定hash|
|[answer-obligations.json](answer-obligations.json)|作者字面义务与独立算术；H01完整18项金额矩阵，H13两SKU×两期×三日共12项日事实/覆盖|
|[prepare.py](prepare.py)|只构造原始输入和独立行算术，不执行图；已有cases时拒绝覆盖|
|[provenance.json](provenance.json)|日期/SKU/金额变体、作者身份和已执行数据去重记录|
|[manifest.json](manifest.json)|锁定源码、题包、目标、范围契约和预算祖先|
|[preflight-20261023.json](preflight-20261023.json)|零调用预检；不是正式成绩或付费授权|

H13授权为 `W131`、`W132`，不会再从开发源继承单SKU范围。
评分不依赖生产意图解析：要求每个授权SKU、两期每一天的完整原始行覆盖及程序金额事实。漏掉对象/日期或伪造事实不能满足此断言。
原Expected结构及旧题包均不变，新范围断言仅由新题包显式选择。

|冻结字段|值|
|---|---|
|实现SHA256|`e9bcf8aa1b7ebeb6b53903b33a15980c7908ac87e1ede658004711d10abf353c`|
|cases SHA256|`115659f2201871b57a9765d0f0d27fe50debb06ba3d66dd871136d98f9e7f0d7`|
|范围契约SHA256|`ef89d71072581b444d41efe6c41833fb1a46c47fbd4a64fa8ee0d69d4cdf7dbb`|
|作者义务矩阵SHA256|`a3b8726f7c76a20c929bbb062e068333a6fede399b1000d4a3773608f8a8023e`|
|准备脚本SHA256|`10c9c94b1eae427b9bec1848c602a4862582570a12427781e96fa6a9c1211621`|
|累计token/现有上限|8,613,150 / 10,000,000|
|累计HTTP / 未知|1288 / 0|
|本次准备付费请求|0|
|上一完整轮观测token|1,339,795|
|单请求保守预占|1,051,576|
|下一完整轮估计累计|11,004,521，较现有上限多1,004,521|

新包只承接v11的扣费，未继承旧Task或质量分数；没有创建预算祖先占用文件，没有增加累计上限。
预检PASS只证明冻结一致，`proposed_limit_estimated_sufficient=false`、`authorization_required=true`及`release_eligible=false`不变。
以上是额度估计，不是实际费用或能够完成整轮的保证。

准备时核对1168份已执行业务输入，无精确数据hash复用。材料仍是AI代码作者根据开发题族制作的合成数值/日期/SKU变体，**不是外部业务盲测**；不同hash不证明语义独立。
题号H01—H20仅在本包内有效，必须结合题包hash、实现版本与run_id识别。

零请求复核：

```bash
.venv/bin/python -m evaluations.diagnosis_plan check \
  --directory docs/acceptance/diagnosis-v12-h01-h20
```

命令不加载密钥、不创建Task、不发送请求。源码、题面、预期、范围契约或历史扣费变化时拒绝沿用计划。
本地修复与正式模型边界见[40号记录](../../40-v12-planning-completion-repair-and-validation.md)，旧v11正式结果见[39号记录](../../39-v11-formal-acceptance.md)。
