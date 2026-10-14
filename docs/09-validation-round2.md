# 第二轮实施与验收记录

日期：2026-10-14（Asia/Shanghai）。开发依据：[08开发文档](08-development-plan.md)。
用户确认先完成本地开发，真实联调待其配置。以下是已执行的开发验证，不是生产验收。
第一轮记录保留在[06](06-validation-report.md)，本文件为当前状态入口；代码仍未提交或推送。

## 实施结果

| 阶段 | 已完成代码 | 验收证据 |
| --- | --- | --- |
| M1 事实绑定 | fact_catalog、实体/单位/周期/快照引用、权威事实句渲染 | 商品互换、单位/周期/快照/数值错配回归通过 |
| M2 业务完整性 | 部分记录披露、完整日期覆盖、前期比較准入、快照提示、风险排序后限量 | 缺少一个销售日不产生覆盖预测；第100个之后的高风险商品进入结果；SQLite/PG均验证 |
| M3 研究与上下文 | 来源分配/窗口/选择清单、逐字支持片段、结构化发现与冲突、research.json | 晚到公开来源进入包；未知/伪造摘录拒绝；协议模型证明draft实际收到研究发现 |
| M4 评估 | workflow/single/multi、32固定用例、人工评分表、三轮严格对比工具 | 契约用例通过；比较器拒绝demo、缺标签、重复轮次及配置不一致；不填充虚假评分 |
| M5 交付 | 三策略界面、中文文档、SQLite离线备份恢复、PDF长中文换行 | 浏览器/API/Worker通过；恢复后继续人工复核且不重复研究；PDF下载及视觉核对 |

## PASS：实际执行结果

- 全量后端：**49 passed，15.78秒**，临时真实PostgreSQL16.2，其中4条PG测试使用
  随机schema与非超级用户应用角色。覆盖任务锁/检查点/API、并发预算、新业务排序与缺数。
- PDF列表与中文换行调整后，另运行导出/恢复聚焦测试：**5 passed，3.12秒**。
- 最终固定离线评估：**32/32**；run_id `a911077e-ec39-477d-ac94-ab8fb88f8dfa`，
  生成于UTC `2026-10-14T06:54:24Z`。保留完整任务/答案/证据/事件/研究发现与指纹于
  `evaluations/results/round2-demo.json`（Git忽略）。中位17.5ms是本地模板/工具耗时，
  不能表示模型延迟、线上性能或成功率。
- 浏览器：**1 passed，12.4秒**。本机Chrome、真实隔离API/Worker/React：登录、草稿
  复核、证据、PDF、刷新事件重放和390px移动布局；未使用真实模型。
- 前端TypeScript/Vite构建、Prettier，后端Ruff、diff检查通过。本机Node26有
  module.register弃用提示，构建成功；Node22远程CI尚未执行。
- 恢复演练：私有新目录备份业务SQLite、checkpoint、产物，哈希校验与SQL完整性验证；
  恢复后登录及PDF下载可用。另验证waiting_review恢复后继续导出，业务工具计数不变。
- 桌面/移动截图与最终三页PDF经人工视觉核对；修正了长中文列表项单独换行的空项目符号。
  PDF视觉使用PDFium；本机Poppler缺少Adobe-GB1映射包，未计其中文渲染为通过。

事实绑定反例现状：给收纳包180天的正确引用，却把text写成保温杯180天，直接校验
会拒绝；live指标句由程序根据通过绑定的引用生成，所以错误商品文案不会作为权威数字
句交付。绑定不能证明模型选中了用户需要的事实，也不能验证任意自由分析，需要质量评估。

## FAIL

本轮当前已执行的检查没有遗留FAIL。实施中的预期变更（库存风险排序、增加research.json）
已同步旧测试契约；新增实质反例没有通过修改期望数值绕过。

## BLOCKED / 未验

| 项目 | 状态与条件 |
| --- | --- |
| 真实模型/Tavily/RAGFlow | 用户选择后续配置；本轮没有真实凭据，未发起外部模型或搜索请求 |
| 模型答案质量/冲突判断/多Agent收益 | 只有脚本模型与合成标注文件的协议验证；需真实运行和人工标签，不能给质量提升数字 |
| 外部来源冻结与公平比较 | 当前live搜索会变化；结果保存证据快照且比较输出警示，尚无固定搜索回放功能 |
| Docker/Compose/HTTPS/GitHub Actions | 当前没有Docker/域名，没有上传或触发CI；配置和烟测入口不能替代运行证据 |
| PostgreSQL生产备份/高可用/负载 | 本轮只演练SQLite离线恢复，未验证生产数据库/文件卷一致性与目标RPO/RTO |
| 全部PDF阅读器兼容性 | 当前CID中文字体未嵌入，PDFium通过；其他渲染器/字体嵌入方案需单独验证 |

## 保留的边界

- 日期完整性以当前商品目录每天应有一行为前提；商品上架/下架生命周期未建模。
  不完整时期的销售金额仅表示已记录部分；库存覆盖暂不给估计。两工具没有共享一致性快照。
- 库存最多展示风险排序后的100个，模型证据包最多展示8个商品的指标；都记录截断。
  原始证据保留完整工具结果，但报告每节仍有8条上限；不能声称所有商品都已解释。
- 词法检索覆盖授权小语料，超过10000片段明确拒绝；尚无向量/重排质量实验证据。
  证据包按28k字符而非精确Token预算，模型上下文与Token费用还需实际提供方验证。
- 逐字引用存在/两来源冲突约束只证明出处格式，不证明蕴含关系、来源真实性或冲突已解决。
- 至少一次执行、未知用量与下一调用准入阈值的费用边界保持；不承诺绝对费用封顶。
- 附件进程仍是资源隔离；自动保留/IAM/对象存储未补齐。本地备份依赖操作人员停止服务，
  脚本检查任务状态不等于确认所有进程停止；备份含敏感内容，必须私有存放。

## 重复验证入口

```bash
uv run ruff check app tests migrations evaluations scripts
uv run ruff format --check app tests migrations evaluations scripts
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
uv run python -m evaluations.run --output evaluations/results/round2-demo.json
cd frontend
pnpm format:check
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

普通pytest不提供TEST_POSTGRES_URL时显式跳过4条PG用例；本轮使用临时PG运行了全部49条。
真实质量标注/对比见[04](04-evaluation.md)，备份恢复与升级边界见[05](05-deployment.md)。
