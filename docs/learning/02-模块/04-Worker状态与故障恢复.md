# 02 模块 · Worker 状态与故障恢复

前置：[经营工具篇](03-经营工具与数据口径.md)。业务问题：两个 Worker 如何领取任务，进程崩溃后谁能接管，旧进程恢复后还能不能写入？

## Part A：先认清四个 ID

| 标识 | 用途 | 能否当作执行权限 |
| --- | --- | --- |
| `conversation_id` | 会话与附件范围 | 不能 |
| `task_id` | 一次分析任务、主图线程 | 单独不能 |
| `lease_token` | 本次领取的执行身份 | 还需匹配有效期与任务状态 |
| `artifact_id` | 下载某个登记产物 | 还需当前用户的任务归属检查 |

`RunContext` 是服务端构造的不可变 dataclass，包含任务、用户、组织、会话与租约。它沿 Worker、图和 Gateway 传递，不由模型填写。

## Part B：按顺序读领取与执行

| 顺序 | 文件与方法 | 本次关注点 |
| --- | --- | --- |
| 1 | [repository.py](../../../app/storage/repository.py)：`claim` | 可领取条件、行锁、随机 token、领取次数 |
| 2 | 同上：`validate_task_lease`、`_fenced` | 写前检查哪些条件，锁何时持有 |
| 3 | [worker.py](../../../app/tasks/worker.py)：`run`、`process` | 并发槽、执行协程、心跳、超时 |
| 4 | 同上：`checkpoint_store` | PG 与 SQLite 的 Saver |
| 5 | [checkpoints.py](../../../app/tasks/checkpoints.py)：`LeaseFencedSaver` | 检查点也必须保护 |
| 6 | repository：`finish/cancel/review` | 最终状态如何提交 |

### B1. 领取条件

`claim` 查最早的 queued 任务，或租约已经过期的 running/cancelling 任务。PostgreSQL 的 `FOR UPDATE SKIP LOCKED` 跳过别的领取事务已锁住的行。随后还有带旧 token 和状态条件的更新，成功才得到新的 `RunContext`。

SQLite 使用短写事务与条件更新支撑本地开发；不能从本地结果推断 PostgreSQL 的并发语义已经验证。

达到领取次数上限、运行截止时间过期，或待取消任务租约过期时，`claim` 会收敛到相应终态。领取次数不等同于模型请求次数；人工复核后重新排队也会产生一次新领取。

### B2. 心跳与总时限

Worker 约每 `lease_seconds / 3` 续租。发现 cancelling 时取消执行协程；失去租约也停止继续执行。`remaining_runtime` 从持久化的 `deadline_at` 得到剩余时间，`asyncio.timeout` 包住执行。

崩溃恢复保留当前运行阶段的绝对截止时间，不会每次接管重新获得完整预算。等待人工复核不计执行耗时；重新批准后的阶段使用累计 `runtime_ms` 计算剩余预算。硬杀前尚未记账的部分不能当作完整精确耗时。

## Part C：状态迁移从触发者看

```text
API 创建：                     queued
Worker 领取：                  queued → running
图遇到人工中断，Worker 收尾：    running → waiting_review
API 登记批准/驳回：             waiting_review → queued
Worker 恢复批准：               running → succeeded / degraded
Worker 恢复驳回：               running → rejected
执行错误：                     running → failed
取消执行中任务：                running → cancelling → cancelled
取消排队/待复核任务：            queued / waiting_review → cancelled
```

表中的“恢复批准/驳回”省略了新的 queued → running 领取步骤。`Repository.review` 只保存决定并重新排队，不在 HTTP 请求中直接继续图。

`degraded` 是交付带明确缺口的报告。它可能来自教学资料限制、语义评审不可用、研究失败或模板回退等不同原因，应继续查 validation 和事件，不能只看标签。

## Part D：画一条接管时间线

```text
Worker A 领取 task，token=A
  → 发起外部请求
  → 心跳中断，租约过期
Worker B 领取同一 task，token=B
  → 根据检查点继续
Worker A 外部请求晚到
  → 写证据/报告/产物时检查 token
  → A 与当前 B 不符，StaleLease，拒绝提交
```

租约保护本地写入权限，不能撤销 A 已发出的外部请求，外部服务仍可能收费。因此不是 exactly-once。

只保护 `save_report` 还不够：如果 A 能覆盖检查点，B 后续可能沿旧状态继续。`LeaseFencedSaver.aput/aput_writes` 同时保护 checkpoint 和 pending writes，并限制线程只能是 `task_id` 或 `task_id:research`。

保护连接在检查租约到内部 Saver 写完之间持续持有业务任务行锁；SQLite 对应写事务。内部 Saver 负责自己的存储，业务表与检查点没有合成一个原子事务。每步使用 `durability="sync"` 降低后续步骤推进时丢失前序进度的风险，但节点内部仍有崩溃窗口。

## Part E：恢复还要处理副作用

| 已发生的动作 | 当前防护 | 仍存在的边界 |
| --- | --- | --- |
| 读取与登记证据 | 指标按任务/工具/周期固定首次快照；文本按稳定键去重 | 空读取、外部请求仍可能重复 |
| 保存报告 | 写前租约检查、图检查点 | 两次提交之间仍可能崩溃 |
| 文件写入与登记 | 每个租约独立目录；任务+名称登记 | 文件写入与数据库登记非原子 |
| 模型已请求、用量未回执 | 恢复时标记未知，阻止新模型调用 | 无法反推提供方真实消耗 |

指标快照复用解决“证据已提交、节点检查点未提交、业务数据又变化”的窗口。Repository 在受租约保护的事务中再次检查首次快照，避免并发写入两个版本。旧账本若已有多个版本，完整保留，但上下文与报告只认最早版本。没有登记证据的空读取可以重放；销售与库存也未合成一个跨工具数据库快照。要分析最新数据，应新建任务。

## 验证入口

先读断言，再选择运行：

```bash
uv run pytest -q tests/test_contracts.py::test_budget_is_atomic_and_old_lease_cannot_write
uv run pytest -q tests/test_checkpoint_fencing.py
uv run pytest -q tests/test_recovery.py
uv run pytest -q tests/test_audit_fixes.py -k 'recovery or sigkill or legacy_metric'
```

恢复测试包含 SIGKILL 和子进程，使用临时测试数据，不要将其改成指向个人运行库。真实 PG 的锁/接管验证另在 `tests/test_postgres.py`，运行条件见[评估部署篇](09-评估部署与备份.md)。

## 关键问题

1. 已有 `task_id` 为什么仍需每次领取生成新 token？
2. 检查完租约立刻释放锁，再写检查点，会留下什么竞态？
3. Worker 被杀和用户主动取消，在恢复动作上有什么不同？
4. 哪些防护限制重复副作用，哪些重复仍无法排除？

<details>
<summary>判定要点</summary>

必须能画出 A 过期、B 接管、A 晚到的顺序；指出业务写与检查点写都要保护，且校验到写入之间要持续持锁。进程崩溃可接管，主动取消应终止；外部请求和节点内操作仍可能重复，不能声称全链 exactly-once。

</details>

下一篇：[LangGraph 流程与人工复核](05-LangGraph流程与人工复核.md)。
