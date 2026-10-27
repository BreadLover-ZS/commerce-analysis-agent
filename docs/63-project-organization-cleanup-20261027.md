# 项目文档整理与临时文件清理（2026-10-27）

**按用户确认范围完成整理与保留核验。** 本轮只改文档入口及索引，清理已确认可再生的缓存／构建／临时测试文件；没有改业务、评测、测试源码，没有调用模型、增加额度、提交或推送。固定语义复核缺陷仍待[62号方案](62-fixed-review-evidence-admission-repair-plan-20261027.md)修复，真实模型轮继续停止。

## 文档整理

- [项目README](../README.md)从201行精简为122行，保留项目说明、启动、主要能力和验证命令，入口集中指向[文档导航](README.md)与[当前状态](status.md)。
- [TASK](../TASK.md)从245行精简为27行，只列当前待办和R1—R4验收要求；旧阶段勾选记录保留在快照中。
- 新增[历史索引](history/README.md)，按阶段列出62份编号文档，以及整理前README／TASK快照；只调整归档快照的相对链接。原始字节也另行保留。
- 新增[验收材料索引](acceptance/README.md)与[文件用途／保留规则](project-files.md)。三个旧用例、评测和进度入口补充历史状态提示，不再把旧“当前”表述作为最新结论。
- 原编号报告、验收脚本、题包、hash清单和原始结果维持原路径，未搬迁其依赖。

## 已清理内容

删除27个确认目标，共 **3,772 个文件／链接，68,742,261 字节（约65.6 MiB，按删除项逻辑大小统计）**。这不是物理磁盘释放量承诺：缓存有链接或共享数据，且部分测试原件转为归档保留。

| 类别 | 处理 |
| --- | --- |
| `.uv-cache/` | 删除可再生成下载缓存；事先确认运行环境没有指向该缓存的符号链接或pth依赖 |
| `.pytest_cache/`、`.ruff_cache/` | 删除测试／检查缓存 |
| 源码树`__pycache__/` | 删除app、evaluations、scripts、tests、migrations下字节码缓存；不进入原始results证据树 |
| `frontend/dist/` | 删除无人使用的构建产物 |
| `frontend/test-results/` | 先逐字节归档9份截图／PDF／状态输出，再删除临时目录 |
| 五处`.DS_Store` | 删除未跟踪的macOS目录元数据 |

9份浏览器输出共 **1,697,780 字节**，归档至 `evaluations/results/project-cleanup-20261027-01/browser-test-results/`，保留原相对结构与hash。本轮未删除唯一失败证据。删除前重新检查Git跟踪／忽略状态及lsof占用；无占用，未跳过待清理项。

## 保留与验证

| 检查 | 结果 |
| --- | --- |
| 实现源码／配置指纹 | 267文件全部不变，`6e2308f2cfcbc3a36c0ef64000bff22ef7a75056e6f8766a4ff4adc952073ef4` |
| 整理前可见Git文件 | 862文件逐项核对；只改获准的README、TASK和三个文档入口，其余不变，原未提交源码保留 |
| 原始评测结果 | **29,777份原文件逐字节不变**，包括调用、费用、工作库、检查点和源码快照 |
| 历史验收封存 | **19份SHA256SUMS／414条内容记录验证通过** |
| 浏览器临时输出 | 9份归档hash与原件一致 |
| 本地配置与依赖 | `.env`内容不变；`.venv`、`frontend/node_modules`保留；Python依赖导入、Vite与TypeScript版本命令通过 |
| Git边界 | HEAD和暂存区不变，未提交／推送 |
| 文档导航与空白 | 链接、锚点与`git diff --check`通过，详见[导航回执](acceptance/project-cleanup-20261027/navigation-check.json) |

保留核验记录：[preservation.json](acceptance/project-cleanup-20261027/preservation.json)；删除清单：[cleanup.json](acceptance/project-cleanup-20261027/cleanup.json)。本轮没有业务代码变化，未重跑完整后端／浏览器测试，也不把依赖导入当作新的业务验收。

原始整理记录在 `evaluations/results/project-cleanup-20261027-01/`：包含整理前文件hash、删除日志、原入口精确副本和浏览器归档。该目录被Git忽略，仍为完整复核所需材料。

## 当前开发和费用状态

最新真实U轮仍为5位执行、91位未执行，追加审计后严格联合仅2／5；问题尚未修复，见[61号结果](61-boundary-v2-U-formal-stop-and-root-cause-20261027.md)。累计已知11,122,121 token，旧UNKNOWN冻结1,051,576，2000万上限内余额7,826,303；严格历史对账仍BLOCKED。本次整理新增模型请求与付费token均为0，G6未放行，G7排除。

以后在[状态页](status.md)维护当前结论，在[TASK](../TASK.md)维护下一步；历史报告按编号引用。不得按“旧”“ignored”“日志”“JSON”标签直接清理模型账本与验收文件。
