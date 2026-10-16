# 启动、部署与运维

## 本地学习（SQLite）

```bash
cp .env.example .env
uv sync --frozen --python 3.12
uv run python scripts/dev_backend.py
```

脚本先迁移/初始化，再启动 API 和 Worker。另一个终端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器访问 http://localhost:5173。初始账号 demo@example.com / demo-commerce-2026。
第二隔离组织账号 other@example.com / other-commerce-2026，用于验证资源归属。
这些是公开的本地演示凭据，生产配置会拒绝默认密码与自动播种。

也可以分别启动 `uv run python -m app.cli init`、
`uv run uvicorn app.api.server:app --host 127.0.0.1 --port 8000`、
`uv run python -m app.tasks.worker`。只有 API 运行时，任务会排队，不会执行。

## 模型请求超时

`.env`可配置`MODEL_READ_TIMEOUT_SECONDS=60`（1–120秒）和
`MODEL_CONNECT_TIMEOUT_SECONDS=5`（1–30秒）。读取值约束等待响应数据的无活动时长，
不等同于任务总耗时；`MAX_RUNTIME_SECONDS`仍限制整个任务。SDK自动重试为0。
模型超时且没有用量回执时，后续模型调用被阻止，费用以服务商账单为准。
安全错误事件包含timeout_phase和elapsed_ms；unknown表示未取得可确认的底层阶段。
本地测试和真实验证范围见[超时记录](14-model-timeouts.md)。

## Docker 本地部署（PostgreSQL）

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d --build
```

访问 http://localhost:8080。服务包括 PostgreSQL、一次性迁移/初始化、API、Worker、
前端代理。数据库不发布宿主机端口，应用角色不具有超级用户/创建数据库/创建角色权限。
运行数据在 pg_data 和 runtime_data 卷中。RAGFlow 是可选外部服务，不在这个 Compose 中。

### 代理地址与登录限流

Compose 使用固定的内部代理地址：`COMMERCE_NETWORK_SUBNET=172.30.0.0/24`、
`COMMERCE_FRONTEND_IP=172.30.0.10`、`COMMERCE_INGRESS_IP=172.30.0.11`。
如与宿主机网络冲突，应同时调整网段和两个地址，保持地址位于该网段且互不相同。
API 的 `FORWARDED_ALLOW_IPS` 仅信任前端地址；前端 Nginx 仅信任入口地址。
Nginx 将识别出的客户端地址覆盖写入 `X-Forwarded-For`，直接访问前端时伪造的头不会进入限流身份。
生产链路为客户端 → Caddy → Nginx → API，API 不发布宿主机端口；不要把信任范围改成 `*`。

Nginx 配置由镜像的模板入口生成；`NGINX_ENVSUBST_FILTER` 只替换入口地址，
保留 `$remote_addr` 等 Nginx 运行变量。客户端真实地址、HTTPS 协议与 Cookie 行为仍须在目标环境验证。
本次本地没有 Docker，容器实际链路尚未验收，详见[修复验证记录](20-audit-fixes-validation.md)。

已有 Compose 网络升级前，应先完成或取消活动任务，并按下文停服备份；
再执行 `down`（不加 `-v`）和 `up -d --build`，让新 IPAM 与代理地址生效。
仅重建某一个容器不能可靠替换旧网络。该操作保留数据卷，但会中断服务，应在维护窗口执行。

下面的代理探针会消耗登录失败限流额度，只用于可丢弃的 demo Compose，不能对正在使用的环境运行：

```bash
uv run python -m scripts.proxy_smoke --confirm-isolated-compose --mode untrusted
docker compose --env-file .env -f docker/docker-compose.yaml --profile verification run --rm --no-deps proxy-smoke
```

第一条从宿主机伪造转发头，验证不能绕过同一客户端桶；第二条占用保留入口地址，
验证不同客户端分桶及同账户跨 IP 限流。`proxy-smoke` 与生产 Caddy 使用同一入口地址，
不得同时运行，也不要给该探针命令叠加 production 配置。这两项不替代公网 HTTPS 验收。

```bash
docker compose --env-file .env -f docker/docker-compose.yaml logs -f api worker
uv run python scripts/container_smoke.py
docker compose --env-file .env -f docker/docker-compose.yaml stop
```

不要用 down -v 停止需要保留数据的环境。首次创建卷时才运行 PostgreSQL 角色初始化；
修改 APP_DB_PASSWORD 不会自动改变已有角色密码。使用数据库管理员流程轮换密码。

## 生产或公网试用

配置实际域名及 HTTPS 的 ALLOWED_ORIGINS，设置独立 SESSION_SECRET、
BOOTSTRAP_PASSWORD、POSTGRES_PASSWORD、APP_DB_PASSWORD。数据库密码使用 URL-safe
字符，避免连接字符串解析问题。SEED_DEMO=false；原始数据由运营者通过 CLI 导入。
需要 Docker Compose 支持 `!reset` 的版本（2.24.4+）。

```bash
docker compose --env-file .env -f docker/docker-compose.yaml -f docker/production.yaml up -d --build
```

production 配置强制 PostgreSQL、Secure Cookie、独立凭据和禁止自动播种。Caddy
负责证书与 TLS，前端 HTTP 端口不直接公开。域名的 DNS、80/443 访问及证书签发须在
实际部署环境验证。首版定位个人展示与小规模试用，未验证高可用/大规模负载。

## 导入自己的数据

JSON 格式见 `data/import-example.json`，商品/SKU与日聚合数据经过 Schema 验证，
一次事务完成 upsert。它是运维 CLI，未暴露给 Agent 或普通用户 API。

```bash
uv run python -m app.cli import-commerce --tenant demo --sample --file data/import-example.json
```

上述文件是样例，必须使用 --sample。真实数据使用实际文件及组织键，不加 --sample。
production bootstrap 组织键为 primary；多用户可通过以下命令加入同一组织：

```bash
uv run python -m app.cli add-user --email colleague@example.com --tenant primary
```

密码通过终端隐藏输入。初始账户仅在首次创建时写入，修改 BOOTSTRAP_PASSWORD 不会
重置已有密码。当前未实现自助密码重置/账户管理 UI，需要继续完善后用于正式团队服务。

## 数据保留与恢复

业务库保存会话、任务、事件、证据和提取文档；文件卷保存产物和研究工作目录。
备份必须覆盖两者；仅备份检查点不能恢复文件，仅备份文件不能恢复任务和权限。
本地 SQLite 检查点在 DATA_DIR/checkpoints.sqlite；PostgreSQL 在同一数据库 schema。

不要对活跃任务做清理。SQLite和PostgreSQL均有离线备份恢复CLI；当前没有自动保留周期、
对象存储或定时备份调度，运营者仍需设置卷容量监控、备份频率和保留策略。

本地SQLite离线备份（先停止API和Worker，确认没有running/cancelling任务）：

```bash
uv run python -m scripts.backup_local create /private/tmp/commerce-private-backup --services-stopped
uv run python -m scripts.backup_local restore /private/tmp/commerce-private-backup /private/tmp/commerce-restored
```

工具只接受新目录，备份包含database.sqlite、runtime（检查点/产物/工作目录）及哈希清单。
不备份.env；恢复后配置DATABASE_URL为恢复目录的database.sqlite，DATA_DIR为其中runtime。
--services-stopped是操作人员声明，工具不能证明所有进程已停；跨数据库/文件的一致性
依赖离线前提。通过恢复演练验证人工复核可继续、不会重跑已完成研究、PDF下载可用。
备份包含用户/文档/会话等敏感数据，使用私有目录，不上传Git，也不用于生产PostgreSQL。

### PostgreSQL离线备份恢复

先停止API、所有Worker及其他数据库/文件写入者，等任务离开running/cancelling状态。
`--services-stopped`是操作者声明，程序不能证明其他进程已经停止。工具将业务表、
LangGraph检查点与运行文件一起备份；检查登记产物的大小和SHA256，拒绝符号链接和覆盖。
备份保留密码哈希、文档与会话，只能保存在可信私有目录；不备份.env或数据库角色。
SHA256只校验损坏，不认证来源。**只恢复可信备份：数据库dump可以包含可执行SQL。**

宿主机需要PostgreSQL16的`pg_dump`和`pg_restore`，或通过`--pg-bin`指定其目录。
后端容器已包含客户端与运维脚本。连接账号须能读取业务/检查点对象；恢复账号须拥有
事先创建的空目标数据库，应用账号不需要超级用户或创建数据库权限。

```bash
# .env中DATABASE_URL指向待备份的PostgreSQL，DATA_DIR指向对应运行目录。
uv run python -m scripts.backup_postgres create .backups/snapshot-01 --services-stopped

# 用管理员流程另外创建空库，再将连接配置写入私有.env.restore。
# 该配置指向新库；不要先执行app.cli init。新运行目录也不能预先存在。
# 清除可能覆盖配置文件的同名进程环境变量。
env -u DATABASE_URL uv run python -m scripts.backup_postgres --settings-file .env.restore \
  restore .backups/snapshot-01 /private/tmp/commerce-restored-runtime --services-stopped
```

恢复后，把服务的DATABASE_URL指向新库，DATA_DIR指向新运行目录；保留原库/卷以便回滚。
先检查登录、旧任务/PDF下载、跨账户拒绝访问和人工复核继续执行，再切换入口。
工具使用`pg_restore --single-transaction --exit-on-error`，拒绝非空库，不执行删除覆盖。
数据库提交与运行目录发布不是一个事务：恢复命令发出后失败，会保留私有暂存目录并输出
其位置。此时保持停服，核对目标库是否已提交及暂存文件，不要直接重复恢复或删除暂存目录。
恢复前其他步骤失败会清理本次暂存文件。原库和备份始终保留。

默认Compose数据库不发布端口，备份可以从一次性运维容器执行。Linux宿主机先创建专用
`.backups`目录并为容器UID/GID 10001设置写权限和0700权限；不要挂载包含其他文件的目录。

```bash
docker compose --env-file .env -f docker/docker-compose.yaml stop api worker
docker compose --env-file .env -f docker/docker-compose.yaml run --rm --no-deps \
  -v "$PWD/.backups:/backups" api python -m scripts.backup_postgres \
  create /backups/snapshot-01 --services-stopped
docker compose --env-file .env -f docker/docker-compose.yaml up -d api worker
```

容器恢复时同样准备新库和持久化的新运行卷，并将其挂载为恢复目标；不要将恢复目录放在
一次性容器的/tmp后退出。CI的`container_backup_smoke`专用于隔离教学库，临时恢复结果会
销毁，不能作为正式恢复命令。真实恢复与失败分支证据见[交付验收](18-python-delivery-validation.md)。

旧版运行中任务应在升级前完成/停止；新MetricReference要求实体/单位/周期维度。
历史已导出文件仍可下载，不将缺少事实目录的旧任务自动认定为新标准通过。

取消通过持久化状态通知 Worker。正在进行的异步模型/HTTP 请求可取消，本地线程中
已经开始的 PDF 处理仍可能结束，但租约校验阻止它提交取消后的产物登记。

## 检查命令

```bash
uv run ruff check app tests migrations evaluations scripts
uv run ruff format --check app tests migrations evaluations scripts
uv run pytest -q
uv run --with pgserver==0.1.4 python scripts/test_embedded_postgres.py tests
```

也可以设置TEST_POSTGRES_URL指向一次性测试服务，TEST_PG_BIN指向16版客户端目录再运行
pytest。测试管理员需要CREATEDB/CREATEROLE；测试会创建并删除随机数据库、schema和角色，
不能指向生产数据库。应用恢复则使用无超级用户/建库/建角色权限的账号。CI使用PG16服务。

隔离HTTP教学负载（Linux/macOS）：

```bash
uv run python -m scripts.load_smoke --requests 20 --concurrency 4 --workers 2 \
  --output evaluations/results/load-sqlite-01.json
uv run --with pgserver==0.1.4 python -m scripts.load_smoke --backend postgres \
  --requests 20 --concurrency 4 --workers 2 --output evaluations/results/load-pg-01.json
```

工具自建临时数据库、API和Worker，固定demo且不继承模型密钥/代理/业务连接，不接受外部URL。
同一幂等键重放必须返回同一任务，完成后验证PDF内容及哈希，成功/失败均停止子进程并清理
临时库；输出文件不得覆盖历史。上限200任务、16并发、4个Worker，每Worker执行并发1。
吞吐和延迟只针对当前机器的闭环教学负载；端到端包括排队、轮询与下载，排除启动和登录，
P50/P95为成功场景的nearest-rank统计。失败数必须一起看，不代表真实模型延迟或生产容量。

浏览器测试：

```bash
cd frontend
pnpm exec playwright install chromium
pnpm test:e2e
```

它自动启动隔离 API/Worker 和前端；使用已安装 Chrome 时可设置
COMMERCE_BROWSER_CHANNEL=chrome。后端环境为临时目录，测试完成自动清理。
