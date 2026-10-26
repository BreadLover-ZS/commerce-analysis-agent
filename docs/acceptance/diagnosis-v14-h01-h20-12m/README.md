# v14完整正式轮授权计划（2026-10-25）

执行结果：96/96 Task已完成，原自动83/96、诊断47/60、关键6/6，G6未通过。累计9,768,379/12,000,000 token、1441 HTTP、UNKNOWN0，详见[47号结果](../../47-v14-formal-acceptance-20261025.md)。以下保留发送前授权/冻结记录；祖先预算已被本轮承接，不能再次用该计划启动或把Q输入当作新保留集。

用户在明确“v14真实模型正式测试：20题、96 Task，累计上限提高到1200万token”后回复“可以。开始吧”。授权见authorization-20261025.json。

本目录是原v14准备包的授权预算版本，cases、semantic-contract、answer-obligations和provenance逐字节一致。原1000万manifest和准备材料不改写；本目录重新冻结1200万预算。

固定源码提交62d98051982c4a8a277df6cb88fe37030185241a；实现指纹1ae1b105caeda30f3483e8f10edd1dbacbeaf17e3bb2035e02136771275c4a8c。业务代码、评分器和门槛不变。20题各3次诊断，加12道共同题各3次固定对照，共96 Task；G7排除。

历史累计8,613,150 token、1288提供方HTTP、UNKNOWN0，全部承接。基于上一完整轮及单请求预占估计累计需11,004,521，授权1200万后的投影余量995,479；不是完成或费用保证。默认产品入口不变。

执行使用已有CLI、每题每次隔离SQLite及全局请求/token账本，保留UNKNOWN停止和关键不变量保护。不重试付费失败、不筛选最好一次，不在正式轮中修改源码或评分，不额外提高额度。自动评分、作者侧AI审查和外部业务验收分别记录。

运行输出：evaluations/results/diagnosis-v14-h01-h20-live-20261025-01/。执行后另出完整验收报告；本包由代码作者准备的合成题族变体，不是外部业务盲测。

使用$grill-me核对已明确范围，按$pm/$confidence-check复用现有冻结、执行、账本和审查流程。已核对官方模型参数及JSON接口示例：https://api-docs.deepseek.com/quick_start/pricing/ 、https://api-docs.deepseek.com/guides/json_mode/ 。
