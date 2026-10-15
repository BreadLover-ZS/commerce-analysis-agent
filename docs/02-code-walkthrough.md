# 源码调用链与模块责任

先通过 demo 看懂执行链，再通过协议测试理解真实 Agent SDK 路径，最后进行 live 联调。
demo 不调用模型，不执行 Deep Agents 工具循环；它复用业务工具、LangGraph、校验和导出。

## 一次任务如何执行

1. `frontend/src/App.tsx` 收集目标、周期、来源、公开关键词、策略与复核选项。
2. `frontend/src/lib/api.ts` 发送 Cookie、CSRF 和幂等键。重复网络尝试保留同一个键。
3. `app/api/server.py` 的 `/api/tasks` 获取服务端身份，校验请求，调用 Repository。
4. `app/storage/repository.py:create_task` 锁住用户，校验会话归属、活跃任务上限和幂等。
5. `app/tasks/worker.py` 从数据库领取任务，建立租约/心跳/超时保护。
6. `app/tasks/checkpoints.py` 将检查点/pending writes纳入租约保护，原始Saver负责存储；
   `app/research/graph.py` 从已保存状态启动或恢复 LangGraph。
7. `ToolGateway` 使用不可由模型填写的 RunContext 绑定用户、组织、会话和租约。
8. 经营工具调用 `app/commerce/service.py`：静态 SQL、只读事务、Decimal/cents 计算。
9. 检索工具保存原始依据，再返回 evidence_id。RAGFlow 使用片段检索而非嵌套问答。
10. `app/research/agents.py` 创建受限 Deep Agent：可信 Skills、只读文件工具、预算中间件。
    `research/meter.py` 的模型回调负责请求准入/用量/延迟，覆盖 SDK 内部摘要；
    agents.py 中间件单独记录委派。
11. `research/context.py` 选择指标、来源和原文窗口；`synthesis.py` 验证研究发现的逐字引用。
    findings/conflicts/gaps保存在LangGraph state，draft实际接收这些发现，导出research.json。
    模型gaps只是候选，不强制追加到报告；操作失败与确定性工具缺口分别保留。
12. `app/research/evidence.py` 核对完整事实元组，按来源渲染指标句，并区分事实、分析和建议。
13. 复核通过后导出。API 只按 opaque artifact_id 下载，不接受文件系统路径。

## 数据结构

`storage/models.py` 的关键表：

| 表 | 责任 |
|---|---|
| users / auth_sessions / login_attempts | 组织身份、可撤销登录、登录限流 |
| conversations / tasks | 会话归属、请求、状态、租约、预算与草稿 |
| task_events | 持久化执行轨迹；id 是事件游标 |
| evidence | 来源、片段、定位、结构化数据、内容哈希 |
| artifacts | 文件登记、大小、哈希、租约目录定位 |
| products / daily_sales | 商品库存快照、按记账日聚合的经营数据 |
| documents / document_chunks | 内部规则与会话附件的可检索片段 |

LangGraph 管理自己的 checkpoint 表。Alembic 管理业务表，两者不能混为“一个任务队列”。

## 经营口径

- 金额以整数分存储；Decimal 转换为元并保留两位小数。
- 净销售额 = 支付金额 - 本周期记账退款金额，未扣经营成本，不是净利润。
- 退款件数比例 = 本周期退款件数 / 支付件数，不是按订单 cohort 计算的退款率。
- 库存覆盖天数 = 快照库存 / 周期日均净销量，只是历史估计。
- 无记录、部分日期、有完整记录但零销量分开；缺日期或没有正净销量时不估计覆盖天数。
- 前期比较使用相同长度窗口，仅在两期完整且前期金额为正时计算变化率。
- 库存按缺数、缺货、积压、正常排序，风险排序发生在100个商品展示上限之前。
- 每个业务工具使用独立只读事务；未承诺跨工具共享同一数据库快照。

固定九月样例：支付660件、退款6件、净销售额83766.00元、退款件数比例0.91%。
杯/包/灯的库存覆盖估计分别为1.85/180.00/25.00天。验证见 `tests/test_contracts.py`。

## 证据校验

原始资料 → Gateway → evidence表 → 报告 claims → 校验 → 前端依据按钮/文件。
同任务、同定位、同内容的公开证据去重，同时保留命中的公开关键词。
内容哈希对应实际保存的片段；经营 evidence 还保存完整结构化指标数据。

规则能够证明：引用来自当前任务、必需章节存在、经营指标的商品/单位/周期/快照与
数值匹配，指标句按程序渲染。原文数字检查仍只提供基础防线，不能证明文本语义对应。
Schema提前拒绝analysis/recommendation携带指标引用，指标只在fact中呈现。
语义复核覆盖claims与limitations，参考授权来源、实际选择与证据清单；对已有规则
误报缺口会拒绝。修正接收原草稿、错误ID/索引和评审理由，所有模型请求仍计入预算。
规则不能证明：来源本身正确、模型推断成立或建议有效。模型语义评审也是辅助手段，
重要结论仍须人工核验。`semantic_status=model_reviewed` 不等于真实业务验收 PASS。

## 权限和错误

Cookie 保存随机 opaque token，数据库只保存其哈希；密码采用带盐 PBKDF2。
修改接口要求 CSRF；组织身份来自数据库，模型不能传入 tenant_id。
任务、事件、证据、产物按用户归属查询；共享内部规则按组织，附件按用户与会话过滤。
公开搜索只能使用用户独立填写的关键词。模型没有任意 URL 抓取、SQL 执行或 Shell 工具。

附件验证格式、文件名、大小、页数、解压后大小与文本量，并在限时子进程中解析。
Linux 增加 CPU/地址空间限制；这是资源隔离，未实现独立 OS 安全沙箱。
原始附件字节不持久化；保存提取片段和位置。若需要原始附件归档，须新增受控对象存储。

瞬态 HTTP 故障至多重试一次；其他工具失败记录类型并保留缺口。未知执行错误形成
failed。权限、过期租约、取消不会作为普通工具失败吞掉。故障原文和密钥不进入事件。
