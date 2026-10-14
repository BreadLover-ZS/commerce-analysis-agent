# 改进后项目的六周学习路线

每个阶段的完成标准是能解释、修改并复现，而不是只读完文件。

## 第一周：业务和完整请求

跑通 README 的 demo，查看一次报告与 evidence.json。阅读 `schemas.py`、
`commerce/service.py`、`api/server.py`、`storage/models.py`。

练习：手算九月净销售额和退款件数比例；解释为什么不能把净销售额当利润。
新增一个可确定计算的指标，补独立预期值测试。能画出前端到工具、再到报告的调用链。
阅读fact_catalog和MetricReference，互换两件商品的数字，说明程序在哪一层拒绝。

## 第二周：任务服务与权限

阅读 `core/auth.py`、`storage/repository.py`、`tasks/worker.py`。
逐项解释：会话/任务/租约 ID、Cookie 与 CSRF、组织/用户范围、幂等冲突、活跃任务上限。

练习：重复提交、切换账户访问旧任务、上传带目录的文件名。解释每项拒绝发生在哪一层。
检查 PostgreSQL 测试怎样使用非超级用户及随机 schema。

## 第三周：LangGraph 与恢复

阅读 `research/graph.py`。追踪 state 每个节点增加了哪些字段、哪些节点会重跑。
运行人工复核与 SIGKILL 测试，观察检查点和业务任务表的不同责任。

练习：在一个新的节点边界注入失败，记录恢复时哪些步骤重做。解释为什么外部请求
仍可能重复，以及 lease_token 能阻止什么、不能阻止什么。

## 第四周：Deep Agents 与上下文

先运行 `tests/test_live_protocol.py`，它使用真实 SDK 和脚本模型，不是模型质量测试。
再配置真实模型与 Tavily，查看 model_start/end、delegation 与业务工具事件。

阅读 `research/agents.py` 和两份 Skills。理解单 Agent 如何关闭默认 general-purpose，
文件工具如何只读，子 Agent 如何隔离上下文，工具身份为什么不交给模型填写。

练习：在固定预算下比较workflow/单/多 Agent；修改一个 Skill 后复测相同任务。只有确实执行
了委派且质量/成本有证据时，才描述“多 Agent 的收益”。

## 第五周：证据与评估

阅读 `research/evidence.py`、`evaluations/cases.jsonl`、`evaluations/run.py`。
手动核对关键结论的原文；制造未知引用、错误数字和有引用但不支持结论的例子。

练习：新增5条真实业务用例。记录请求、工具、证据、报告、人工结论和失败原因，
比较改进前后，不把结构校验通过等同于事实正确。任何正向用例失败先保存证据再修复。
阅读context.py/synthesis.py，用research.json核对研究发现、原文窗口与遗漏。使用
evaluations.review生成空白评分表，完成自己的人工标注，不能把空标签或demo作为质量结果。

## 第六周：部署与面试表达

按部署文档启动 PostgreSQL 版本，观察 API/Worker 分离、重启与文件卷。
真实服务部分完成后更新验收记录，再录制一段经营分析—证据—复核—下载的演示。

至少能解释这些设计取舍：

1. 为什么流程用 LangGraph，复杂研究才用 Deep Agents？
2. 为什么采用受限业务工具，首版没有开放自由 SQL？
3. 同一个 task_id 的检查点与租约有什么区别？
4. 幂等提交和幂等副作用分别保证什么？
5. 为什么不宣称 exactly-once？
6. 引用 ID 有效为什么仍可能回答错误？
7. 简单问题调用多个 Agent 有什么成本？
8. 模型和搜索故障如何降级，哪些错误必须终止？
9. 你验证过哪些能力，哪些仅有协议测试或静态配置？
10. 哪些代码来自上游、AI 辅助、你自己的设计与开发？

与 Java 后端经历衔接时，可以讲任务、幂等、权限、事务、恢复等工程责任；不要把
Python/FastAPI 实现写成 Spring Boot，也不要把演示数据写成真实业务规模。
