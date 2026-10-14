# 项目命名与目录迁移

2026-10-14，用户确认采用直观的功能名称并授权统一项目目录、配置、文件及界面。

| 用途 | 统一名称 |
| --- | --- |
| 产品/页面 | 电商运营分析助手 |
| 英文项目/仓库/目录 | commerce-analysis-agent |
| 完整介绍/简历项目名 | 电商运营分析与竞品研究 Agent 系统 |
| Python项目标识 | commerce-analysis-agent |
| 前端包 | commerce-analysis-agent-frontend |
| Docker Compose项目 | commerce-analysis-agent |
| API标题 | 电商运营分析助手 / Commerce Analysis Agent |
| 报告标题 | 电商运营分析报告 |
| 截图文件 | commerce-analysis-desktop.png / commerce-analysis-mobile.png |

本机目标目录：`/Users/zzds/Workspace/gitStore/commerce-analysis-agent`。
迁移保留Git历史及所有源码；不会提交、推送或修改远端。README/PROVENANCE中的上游
仓库链接保留真实来源，历史验收数据与原始资料不伪装为新命名后的运行结果。
app/commerce、app/research、Skills及迁移文件是业务模块名，不属于旧产品名称。

目录迁移后重建Python开发环境、检查前端依赖入口并运行构建/浏览器流程，避免旧路径
残留在venv脚本与前端产物中。迁移及本地验证已完成。

Codex当前保存的项目入口仍绑定原目录。应用没有可调用的项目路径修改接口，且UI
自动化禁止操作Codex本体，因此需要用户在应用中重新打开新目录；这是应用入口迁移，
不是代码或真实目录改名。无需删除旧对话、重置Git或删除文件。

Compose默认项目名影响命名卷前缀；已有部署应在切换前显式关联原卷或迁移数据。
本机没有Docker且未启动过该Compose，本轮未操作任何数据库卷。

## 本轮验证结果

- 原目录已迁移，新目录及.git存在；HEAD仍为d0f6eed1e14b1b457942ba2a0195f65731aaf444。
- Python虚拟环境已重建，前端依赖已同步，两个残留命令入口已改到新绝对路径。
- 后端认证/报告/PDF/文件/恢复相关测试：16 passed，12.24秒。
- 浏览器登录、复核、报告下载、刷新恢复与移动布局：1 passed，13.7秒；截图已更新。
- 前端TypeScript/Vite构建、Ruff、Prettier、diff和文档链接检查通过。
- API/OpenAPI、Python与前端包名、新页面标题已核对。
- 上游链接和Git origin保留真实来源，本轮未提交、推送或创建GitHub仓库。
- Codex保存的项目路径仍指向旧目录，需用户重新选择新目录；其他文件改名已完成。
