# DeepSeek 适配修复与单条真实验证

日期：2026-10-14（Asia/Shanghai）。本次按用户授权，仅运行 `monthly-sales` 教学模拟
销售用例；使用临时 SQLite 数据库，不读取实际经营数据库或用户上传文件。
代码修改尚未提交或推送。本文不构成生产验收或完整模型质量评估。

## 问题与修复

1. 初始草稿请求返回 `BadRequestError`。代码使用指定工具的结构化输出，而官方 DeepSeek
   默认思考模式不支持指定工具选择；模型工厂现在显式关闭思考模式。原始400响应未保留，
   因此不声称已核对其原始错误消息；修改后实际请求成功、返回完整用量。
2. 非思考模式的普通工具参数出现多余括号，SDK 将其归入 `invalid_tool_calls`，没有获得
   报告。DeepSeek 结构化阶段改用 JSON 输出，并提供完整 Schema；其他服务商保留
   function-calling 路径，普通研究工具仍使用工具调用。
3. JSON 模式首次输出超过每节8条结论的 Schema 上限。明确数量/长度约束，结构化解析
   失败最多进行一次带字段错误反馈的修正，不删减返回字段来绕过校验。
4. 业务校验拒绝分析句中没有指标绑定的阿拉伯数字。原来的修正上下文只含错误ID，
   现在还提供原草稿；数字/日期放在绑定指标的事实句中展示，分析不自行新增阈值判断。

Pydantic Schema、事实维度、引用和数字校验均保持。解析错误事件仅保存字段路径、
错误类型与次数，不写入原始内容。修正请求计入原有模型次数、Token和运行预算；
结构化修正与业务修正分别限次，失败仍回退且不计为模型成功。

接口依据：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、
[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

## PASS：已执行

- 最终全量后端测试：**57 passed，17.97秒**；临时真实 PostgreSQL16.2。
- Ruff、格式与 Git diff 检查通过。新增测试覆盖实际HTTP请求体、服务商/域名区分、
  脱敏错误记录、限次结构化修正和业务修正原草稿反馈；数字绑定反例仍被拒绝。
- 真实模型：`deepseek-flash`，`single` 策略，`monthly-sales` **1/1**。
  run_id：`8a0f5f25-23ff-4500-aa2f-87c524144bcb`；用例耗时 **7.794秒**。
- 实际模型调用 **2次**：草稿、语义复核；输入5095 Token、输出1796 Token，用量完整。
  `draft_fallback_used=false`，没有最终确定性回退；不是模板代替模型回答。
- 人工逐项核对：支付84540元、退款774元、净销售额83766元；支付660件、退款6件、
  净销量654件、退款件数比例0.91%、前期净销售额变化0.00%，均对应保存的指标依据。
  库存/竞品缺口与人工补充建议明确，模拟数据及不能视为净利润的限制已披露。

任务终态为 `degraded`，因为本用例只授权销售来源，缺少库存、竞品和成本资料；
这次并非模型失败或报告回退。它证明该用例的请求、结构化输出、校验与交付链可工作，
不证明模型在其他任务上的质量或稳定成功率。

## FAIL 与保留证据

修复前的请求错误、无效工具JSON、Schema超限和数字绑定失败结果仍保留；没有把旧失败
改记为PASS。最新这条用例通过，未继续运行其他业务用例或完整32条评估。

全部请求、报告、证据、事件与诊断结果位于被 Git 忽略的
`evaluations/results/live-20261014/`；最终结果是 `06-monthly-sales-validated.json`。
诊断原始模型内容只涉及本次模拟资料，不上传仓库。

## 未验与当前配置

- 库存/内部文档真实模型、多 Agent 委派、Tavily/RAGFlow及完整业务质量评估未执行。
- 本次真实模型经过评估器直接调用Worker，未执行工作台的真实模型浏览器验收。
- PostgreSQL用于回归测试，真实模型用例使用SQLite；不将两者合称真实生产链验收。
- 未配置模型Token单价，无法估算实际费用；服务商账单为准。调试各轮已知合计输入
  22721 Token、输出12180 Token，另有最初失败请求缺失用量，不能推断其是否计费。
- 修复后的代码未推送，因此之前GitHub CI成功不能作为本次修改的远程CI证据。
- 用户 `.env` 仍为 `AGENT_MODE=demo`；评估器用 `--mode live` 单独覆盖，未全局开启
  后续工作台请求的付费调用。

## 复测入口

```bash
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
uv run python -m evaluations.run --mode live --strategy single \
  --cases evaluations/results/live-20261014/01-monthly-sales.jsonl \
  --output evaluations/results/live-20261014/recheck.json
```

第二条命令会再次调用模型并产生费用。单条用例文件是原评估集中的 `monthly-sales`
原样摘取；本次没有改动预期数值或评估通过条件。
