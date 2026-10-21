# 诊断运行、维护和恢复

沿用现有 API、Worker、PostgreSQL 和产物卷。这里补充 G7 的新图维护入口；本机演练不能替代目标HTTPS、容器代理链或真实经营验收。

## 启动与探针

先按 `.env.example` 配置环境并执行 `python -m app.cli init`，迁移到head后再开放API与Worker。
现有 `/health/live` 只表示进程存活，`/health/ready` 检查数据库及新表/列是否可用；缺迁移时返回503。
容器入口仍在 `docker/docker-compose.yaml`，init完成后启动API和Worker；可用 `--scale worker=2` 启动多个独立Worker。
每个进程的并发数与全局任务额度分别由 `WORKER_CONCURRENCY` 和冻结的Task预算控制。

新诊断任务采用 `diagnosis-v1` 或 `diagnosis-v2`；原固定输入对照采用 `commerce-review-bundle-v1`。
恢复时按Task保存的四项版本选择契约，不能将v1检查点按v2重新解释。未知版本会拒绝执行。
任务创建后不自动变成live；发模型请求仍受原Task模式、账本、usage和预算门禁约束。

日常诊断界面展示失败代码、步骤、额度、固定输入、未知usage、等待TTL及报告版本。
重点核对长期queued、等待过期、lease反复接管、usage未知、预算拒绝和产物校验失败；不要通过重置次数或删除调用记录“恢复”。
完整运营监控/告警接收方和目标环境仍须由部署方配置，本项目没有自动发送通知。

## 停写维护窗口

1. 停止任务创建入口及API，停止全部Worker，并确认没有在途进程继续写数据库或产物卷。
2. 正常完成、取消或恢复在途任务到明确状态。备份拒绝 `running/cancelling`；STARTED模型调用必须先按现有接管流程处理为已知结果或UNKNOWN。
3. 执行只读恢复审计，再创建一份业务DB、checkpoint和产物的协调备份。
4. 在新数据库/新目录恢复、核对、演练等待任务后，才开放服务。

```bash
uv run --frozen python -m scripts.diagnosis_state_audit --services-stopped
```

该命令不领取任务、不发模型请求。检查支持的图/Schema/Prompt/工具版本、冻结来源内容、调用预占和usage累计、报告哈希、登记文件及等待任务的checkpoint存在性。
UNKNOWN可以保留并备份，已丢失的费用不能补成0。未核对STARTED、伪造usage、未知版本、缺失文件或等待checkpoint会报FAIL。
在线执行该审计可能看到瞬态未完成调用，不能把它替代在线liveness/readiness探针。

## 协调备份与恢复

SQLite只用于本地开发，目标目录须在运行目录之外且尚不存在：

```bash
uv run --frozen python -m scripts.backup_local create /私有备份/批次A --services-stopped
uv run --frozen python -m scripts.backup_local restore /私有备份/批次A /新目录/恢复A
```

恢复返回业务数据库与data_dir路径，须同时改为这两个同批位置；不能继续指向原来的checkpoint目录。
备份含 `backup_id`、文件哈希与 `diagnosis_recovery.state_sha256`；恢复发布目录前再次核对诊断状态。
旧格式历史备份仍可恢复，但没有新诊断核对记录的旧备份不能被称作已完成新图验收。

PostgreSQL工具沿用私有配置、PGPASSFILE和PG16客户端，不将数据库密码放进命令参数或回执：

```bash
uv run --frozen python -m scripts.backup_postgres --settings-file /私有位置/源.env \
  create /私有备份/批次B --services-stopped

uv run --frozen python -m scripts.backup_postgres --settings-file /私有位置/新空库.env \
  restore /私有备份/批次B /新目录/runtime --services-stopped
```

源库的业务表与PG checkpoint表由同一次pg_dump保存，产物卷按同一维护窗口复制并核对。
恢复只接受空库，不drop已有库；数据库已恢复但目录未能发布时，保留私有暂存目录并保持停服，按错误信息核对后处理。
这套工具依赖停服前提，没有实现跨独立在线系统的原子快照。

恢复测试覆盖v1销售、v2库存/规则、waiting_input、waiting_review、已完成产物、已取消任务及UNKNOWN调用账本。
另有隔离PG维护升级测试：v1等待任务经过D3→head迁移后按原契约继续，同时接受新的v2任务。
这是维护升级演练，不是目标环境滚动升级或生产灾备认证。

## 孤立产物清理

取消或旧租约可能留下未登记文件；没有Artifact记录的文件仍不能通过下载API访问。
先看默认dry-run输出：

```bash
uv run --frozen python -m scripts.cleanup_artifacts
```

确认全部服务已停止后，显式执行：

```bash
uv run --frozen python -m scripts.cleanup_artifacts --apply --services-stopped
```

只删除至少24小时、未登记且不属于运行中任务的文件。保留所有已登记报告版本、数据库证据、输入包及近期文件。
运行任务、符号链接、删除前发生变化的文件会阻止操作。目录不删除；没有自动后台清理，也没有把文件删除权限交给Agent。
清理器不替代备份或历史版本归档策略。

## 隔离多Worker演练

```bash
uv run --frozen --with pgserver==0.1.4 python -m scripts.load_smoke \
  --backend postgres --profile commerce_diagnosis --requests 20 \
  --concurrency 4 --workers 2 --output evaluations/results/diagnosis-load-new.json
```

它自行创建临时PG、非超级用户、业务资料、API和独立Worker进程；不能指定现有URL或业务数据库。
校验就绪探针、同键重放、相同输入包、步骤/工具上限、模型请求为0、版本化PDF及哈希，结束后清理所有子进程和临时库。
保留每个成功/失败结果及本机P50/P95，不据此声称真实模型延迟、生产吞吐或多租户容量。
需允许临时本地端口；环境不允许时按BLOCKED记录，不能把监听失败当成产品故障或绕开真实数据库验证。

## 学习与现场演示

源码地图及本人练习见 [24号文档](24-diagnosis-source-map.md)，评测见 [26号文档](26-diagnosis-evaluation.md)。
演示可以从模拟CSV→输入质量核对→诊断步骤/证据→澄清→绑定版本复核→PDF→反馈/新任务追问进行。
另展示一例不可比较、一例规则未核验和一例取消/未知usage，说明停止边界。
自动测试不代替本人练习或业务人员确认；真实数据、真实业务反馈和目标部署保持各自的验收状态。
