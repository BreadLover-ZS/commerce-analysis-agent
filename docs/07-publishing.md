# 上传自己的 GitHub 与成果整理

当前项目发布到 [BreadLover-ZS/commerce-analysis-agent](https://github.com/BreadLover-ZS/commerce-analysis-agent)。
按用户选择，以当前版本创建独立的首个提交，不推送上游 Git 历史；保留 PROVENANCE
中的上游仓库、提交和继承材料说明。不要把继承代码描述为从零原创。
先检查 `09-validation-round2.md`，把真实服务联调中新增的证据补到记录里。

## 发布前检查

```bash
git status --short
git diff --check
git diff --stat
```

确认不包含 .env、模型密钥、数据库、运行日志、原始客户文件或评估原始数据。
`.gitignore` 已排除这些常见运行文件；忽略规则不能替代内容检查。
保留源码、锁文件、测试、迁移、数据样例、文档及来源记录。

本地保留上游远程，自己的仓库作为 origin：

```bash
git remote -v
# origin: https://github.com/BreadLover-ZS/commerce-analysis-agent.git
# upstream: https://github.com/didilili/deepsearch-agents.git
```

上游提交历史仅在本地备份分支 `upstream-snapshot` 中保留，发布只推送 `main`。
后续开发提交到 `main` 并执行 `git push origin main`，不要使用 `--all` 推送备份分支。
06/09 验证记录中“未提交或推送”描述的是对应开发验收时的状态。

## 首次发布前复核（2026-10-14）

- 全量后端测试：49 passed，15.31秒，使用临时真实 PostgreSQL16.2。
- 固定 demo 评估：32/32；仅验证确定性契约，不代表真实模型质量。
- Chrome 浏览器流程：1 passed，13.7秒，包含登录、复核、依据、PDF、刷新与移动布局。
- Ruff、Prettier、TypeScript/Vite 构建及 Git diff 检查通过。
- 待提交文件路径与常见凭据模式检查未发现密钥、运行数据库或日志；不替代完整安全审计。
- 真实模型/搜索/RAGFlow、Docker/公网部署及远程 CI 的状态仍需分别验证。

## README 与简历

推荐突出：业务指标口径、受限工具、状态机/检查点、租约/幂等、证据/数字校验、
故障与评估，以及你后来实际完成的模块。演示视频可以覆盖：提交→草稿→依据→复核→PDF。

“独立开发”应指你能够独立承担并解释的具体设计和实现，保留开源底座与 AI 辅助事实。
以下均不能直接作为当前简历成果：真实客户经营收益、线上 QPS、多 Agent 质量增幅、
真实模型任务成功率、生产高可用，以及尚未实际通过的容器部署。

CI 配置有后端/PostgreSQL、浏览器、容器三个任务；推送后检查它们的实际结果。
项目内静态配置与本地通过的检查，不代表 GitHub CI 或公网部署已经通过。
