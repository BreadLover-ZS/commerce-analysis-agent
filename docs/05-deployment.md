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

## Docker 本地部署（PostgreSQL）

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d --build
```

访问 http://localhost:8080。服务包括 PostgreSQL、一次性迁移/初始化、API、Worker、
前端代理。数据库不发布宿主机端口，应用角色不具有超级用户/创建数据库/创建角色权限。
运行数据在 pg_data 和 runtime_data 卷中。RAGFlow 是可选外部服务，不在这个 Compose 中。

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

不要对活跃任务做清理。当前没有自动保留周期/对象存储/PostgreSQL备份恢复工具，运营者需要
设置卷容量监控、备份与保留策略；自动清理可作为后续独立、有测试的功能。

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

也可以设置 TEST_POSTGRES_URL 指向一次性测试服务运行 pytest。测试会创建并删除随机
schema 和测试角色，不能指向生产数据库。CI 使用 PostgreSQL16 服务运行相同用例。

浏览器测试：

```bash
cd frontend
pnpm exec playwright install chromium
pnpm test:e2e
```

它自动启动隔离 API/Worker 和前端；使用已安装 Chrome 时可设置
COMMERCE_BROWSER_CHANNEL=chrome。后端环境为临时目录，测试完成自动清理。
