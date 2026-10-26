# v15 正式题包准备：未执行

20道T系列合成输入，计划60个诊断Task＋36个固定Task，合计96。仅准备和预检，无图、工具、模型执行。新输入没有进入开发回放；旧Q系列已经执行，只用于本地回归。数据hash不同不能证明相对开发族的语义独立性，不能称作外部盲测。

冻结版本为diagnosis-v15 / schema4 / Prompt-v15 / 数值注册表6，以及commerce-review-bundle-v4。源码指纹见manifest.json，当前为`1bb3bbb8302c4bc5c471dec6d1193636222734c384cb967d07ed74dcb5b85db7`。

H03题面明确要求两期支付、退款、净额及各自差额，作者表据此冻结九项；H06/H07/H17仍接受evidence_gap，独立要求boundary_explained。作者必答项、来源限制、字面口径与生产请求契约分离；业务输入白名单不包含Expected、作者表或侧表。

|文件|用途|
|---|---|
|cases.json|20题业务数据及既有兼容Expected格式|
|semantic-contract.json|schema2侧表，逐题请求/完成/停止口径及诊断、固定完整版本|
|answer-obligations.json|人工按字面选择的对象、必答矩阵、原始行整数值/单位、日明细、贡献及缺口口径|
|prepare.py、provenance.json|作者生成过程、日期/数值/SKU映射、已执行输入查重；禁止覆盖已有题包|
|manifest.json|冻结源码、题目、侧表、计划任务数、历史预算与投影，不授予付费调用权限|
|verify.py、preflight-20261025.json|独立日历计数和整数求和复验、实际版本匹配、历史账本及数据新鲜度|
|check_tampering.py、tamper-controls.json|12项复制元数据篡改控制；不启动任务或HTTP|
|SHA256SUMS|目录完整性|

免费预检：

```sh
.venv/bin/python docs/acceptance/diagnosis-v15-h01-h20/verify.py
.venv/bin/python docs/acceptance/diagnosis-v15-h01-h20/check_tampering.py --output /tmp/commerce-v15-packet-controls-new.json
```

当前账本为9,768,379/12,000,000 token。按上一正式轮用量加单请求预占估计，下轮累计11,975,184，仅余24,816；不是成本或完成保证。本轮没有增额、没有启动真实模型、没有占用预算祖先承接文件。下一步需要另行明确授权正式运行与逐份审查；G6未放行。
