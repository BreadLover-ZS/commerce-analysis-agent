# 项目文件用途与清理规则

| 路径 | 用途 | 保留规则 |
| --- | --- | --- |
| `app/`、`frontend/src/`、`migrations/` | 业务实现、界面、数据库迁移 | 保留全部源码与当前未提交修改 |
| `tests/`、`frontend/e2e/`、`evaluations/`（不含results） | 测试、判定器、协议及正式入口 | 属于实现指纹，不作为临时文件删除 |
| `scripts/`、`docker/`、`data/` | 运行／维护工具、部署和样例 | 保留；“脚本”或“模拟数据”不等于无用 |
| `.env` | 本地配置与密钥 | 保留，不输出内容、不纳入Git |
| `.venv/`、`frontend/node_modules/` | 已安装运行依赖 | 本轮保留，避免清理后无法继续开发 |
| `docs/acceptance/` | 验收回执、hash清单、作者义务、复现／变异脚本及失败证据 | 保留原路径和原件；不得删除所谓“旧失败”或重写成绩 |
| `evaluations/results/` | 被Git忽略的原始调用、账本、源码快照、工作库、检查点与报告 | 保留；预算承接与UNKNOWN核验依赖它，不能按ignored一律清理 |
| `docs/history/` | 阶段索引及整理前README／TASK快照 | 保留；归类以索引完成，不搬迁验收依赖 |
| `examples/`、`docs/knowledge_base/`、`docs/images/` | 上游对照、知识资料与界面说明 | 保留；尚未证明可安全删除的文件不处理 |
| `.uv-cache/`、`.pytest_cache/`、`.ruff_cache/`、源码树`__pycache__/` | 可再生成的下载、测试、检查和字节码缓存 | 确认ignored、未被进程占用、无依赖链接后可清理 |
| `frontend/dist/` | 前端构建产物 | 无服务占用时可清理，下次构建再生成 |
| `frontend/test-results/` | 浏览器测试临时输出 | 先将唯一截图／PDF等验收原件归档并校验，再清理；失败trace不能直接扔掉 |
| `.DS_Store` | macOS目录元数据 | 确认未跟踪后可清理 |

本轮浏览器临时输出已按原字节归档至 `evaluations/results/project-cleanup-20261027-01/browser-test-results/`。整理基线与保留校验在同一原始记录目录，公共结果见[63号记录](63-project-organization-cleanup-20261027.md)。

不使用全仓库 `git clean -fdx`、按文件年龄批量删除或无条件删 `*.log`／`*.json`。这些操作会误删密钥配置、运行依赖、预算账本和验收材料。无用与否先看引用及职责，再看是否能重建。

维护文档时只在[状态页](status.md)更新当前结论，在[TASK](../TASK.md)保留当前待办；编号报告写当时的证据，历史材料通过[历史索引](history/README.md)归类，不再在多个首页重复追加完整进度。
