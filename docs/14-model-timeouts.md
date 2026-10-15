# 模型超时配置与脱敏诊断

日期：2026-10-15。接续[研究复核验证](13-research-semantic-validation.md)。此前两次综合
评估分别约25.76秒和25.28秒在规划请求超时；模型工厂确实固定配置`timeout=25`。
历史事件未保留底层超时阶段，因此无法据此判断是连接、读取、提供方排队或其他原因，
也不能认定延长超时一定能解决问题。

## 改动与配置

| 环境变量/限制 | 默认值 | 范围及含义 |
| --- | --- | --- |
| MODEL_READ_TIMEOUT_SECONDS | 60秒 | 1–120秒；等待响应数据的无活动时限 |
| MODEL_CONNECT_TIMEOUT_SECONDS | 5秒 | 1–30秒；建立连接的时限 |
| 写入时限 | 10秒 | 固定，每次发送数据等待的时限 |
| 连接池等待时限 | 5秒 | 固定，获取连接的时限 |
| MAX_RUNTIME_SECONDS | 180秒 | 保持原配置，Worker任务总执行时限 |

使用HTTPX四阶段Timeout传给现有OpenAI兼容SDK。读取时限不是单次请求或任务总耗时，
模型请求仍受Worker的绝对执行截止时间约束，恢复接管不重置该时间。SDK自动重试仍为0，
超时没有用量回执时继续记录未知用量，并阻止后续模型调用；不把超时请求当作免费。
语义复核、数字绑定和已有次数/Token预算不变。

`model_error`事件增加安全的elapsed_ms；超时诊断增加timeout_phase，只从异常因果链
中的已知HTTPX类型识别connect/read/write/pool。链缺失或无法确认时为unknown，
遍历有长度和循环保护。不会从错误文本猜测阶段，不记录请求体、URL、密钥或服务方
原始消息。旧记录不追写为新诊断，模型调用耗时不等同于整个任务耗时。

评估结果configuration新增读取/连接超时，比较器原有配置一致性检查会拒绝混合不同
超时配置的结果。`.env.example`已提供字段；已有`.env`未设置时使用新的默认值。
本地应用仍为demo，没有全局启用付费模式。

## 实施前检查

未发现重复的超时配置或原因链诊断；沿用Settings、HTTPX、既有模型工厂和预算回调，
不增加运行依赖。模型工厂固定25秒已有源码及运行时对象证据；历史两次超时的服务方
根因未知。本次以改善配置和可观察性为目标，不声称已修复提供方响应。

核对[HTTPX超时定义](https://www.python-httpx.org/advanced/timeouts/)、
[官方兼容SDK的超时/重试用法](https://github.com/openai/openai-python#timeouts)与
[Python任务总时限](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.timeout)，
并以已安装SDK的实际请求扩展字段验证传递结果。对本地实现的置信度达到95%。

## 验证范围

- 初次专项测试：**23 passed，2.83秒**；包含原有服务边界测试。后续新增真实回环HTTP
  保活响应，超时专项文件**14 passed，4.15秒**。
- 测试通过SDK MockTransport观察实际请求的超时配置，注入ReadTimeout后验证只请求
  一次、保存安全阶段/耗时、标记用量未知且拒绝再次调用。没有访问外部模型服务。
- 在同一真实SDK调用链上模拟一直等待的传输，把任务剩余总时限缩短至0.5秒，验证
  Worker取消传输、任务runtime_budget失败且不导出；模拟传输本身不模拟HTTPX网络
  计时器，这项测试验证的是应用总时限。
- 保活回归使用真实回环HTTP服务，每0.1秒发送空行，读取时限1秒、任务剩余时限2秒。
  连接持续收到字节，Worker最终以runtime_budget取消请求，1次调用、未知用量、无产物。
  仅访问127.0.0.1，不是用真实提供方制造超时。
- 最终后端全量：**126 passed，28.86秒**；临时真实PostgreSQL16.2，包含5条PG测试。
- 固定demo：**32/32**，run_id `51ca792f-7be2-4357-81ee-3fec18a9978e`；
  读取配置60秒、连接5秒，属于确定性本地评估，不是模型质量证明。
- Ruff、格式与Git diff检查通过。远端CI在本轮发布时另行核对。

## 真实模型

上一条授权的一次综合用例已在13记录中执行并失败。用户在本地配置/回归完成后另行
明确授权一次60秒读取配置的综合补货复测，最多8次模型请求，失败后保留证据并停止。
本次run_id为`32b6b827-5b66-419e-857e-c01b1e541798`，task_id为
`4b007633-3eb9-4cf5-a57f-8c396605f787`，模型deepseek-flash，single策略。

**FAIL：0/1。** 首个规划请求在任务耗时120.477秒后以APITimeoutError失败，
model_error记录timeout_phase=read、请求elapsed_ms=120332。模型请求1次、工具0次，
没有研究/报告产物；usage_complete=false，没有用量回执，不以0 Token推断免费。
research_execution、research_semantics、agent_execution均false。人工核对了事件、
失败阶段及产物缺失；因未进入研究阶段，仍不能验证F1/F8的真实语义判断效果。
保留JSON与人工说明，未追加第二次付费评估或扩大其他真实用例。

实现指纹`a625a74ad096d50ba7d94cf1e811f6d00a64504bff63d02a80e4acac1d7a1d93`，
与本轮32条demo一致；新增保活测试及文档不改变覆盖app/evaluations/uv.lock的指纹。

本次任务时长超过60秒并不违反读取无活动时限。[DeepSeek官方保活说明](https://api-docs.deepseek.com/quick_start/rate_limit/)
记载非流式请求等待期间可能持续返回空行；这与观察到的行为相容，但本次未保存原始
响应，不能认定具体请求确实收到保活或确定其服务方根因。任务未触及180秒总时限。

随后只读GET /models返回200（138毫秒），配置模型在列表中；仅能证明当时认证请求
及模型列表可达，不能证明生成服务正常。探测未请求生成，不产生模型输出记录。
本轮不再需要用户提供密钥，真实质量验收仍待生成请求可完成后再进行。
