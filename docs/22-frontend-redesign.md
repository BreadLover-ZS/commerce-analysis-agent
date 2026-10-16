# 前端工作台重构与验证

日期：2026-10-16。

用户确认采用浅色专业运营工作台，并将新建配置与任务报告分开显示。

## 界面与交互

- 使用灰白底、细分隔线和蓝色主操作，统一 Ant Design 主题及原生表单样式。
- 新建分析按分析设置、数据准备、分析要求组织；高级研究显示对应的来源与策略选项。
- 数据预览以表格展示名称、性质、规模、周期和版本校验；确认后的数据质量检查继续约束提交。
- 任务详情集中展示报告、状态、章节目录、证据引用、下载和执行事件。
- 记录列表支持关键词搜索、选中状态和任务状态统计；手机端默认收起，可展开查看记录和退出账户。
- 登录页、错误诊断、证据抽屉与桌面/手机布局统一调整。

## 组件职责

| 文件 | 职责 |
| --- | --- |
| `frontend/src/App.tsx` | 登录状态、接口调用、轮询、新建配置和视图切换 |
| `frontend/src/WorkspaceSidebar.tsx` | 记录搜索、选中导航、手机折叠和账户操作 |
| `frontend/src/TaskReport.tsx` | 报告、目录、复核、取消、下载、事件和证据入口 |
| `frontend/src/CommerceDataPanel.tsx` | 数据生成/导入、版本预览与确认、质量检查 |
| `frontend/src/presentation.ts` | 任务状态文案与颜色映射 |
| `frontend/src/Icon.tsx` | 本地图标 |
| `frontend/src/main.tsx` / `styles.css` | 组件主题、页面样式与响应式布局 |

使用现有 React、TypeScript、Vite、Ant Design 依赖，后端接口及数据契约沿用现有实现。

## 验证

在仓库根目录执行：

```bash
pnpm --dir frontend build
pnpm --dir frontend format:check
COMMERCE_BROWSER_CHANNEL=chrome pnpm --dir frontend test:e2e
git diff --check
```

本轮结果：构建、格式和差异检查通过；Chrome 浏览器回归 **11 passed**。
浏览器检查包括登录、CSV 错误反馈、模拟数据预览确认、数据版本绑定、质量检查、
草稿复核、证据原文、PDF 文件头、刷新恢复、配置/报告切换、记录搜索、手机折叠、退出登录，
以及 320、390、768、1024、1440px 下的布局检查。

逐张检查了登录、新建分析、报告、手机页面和诊断截图，修正复选框布局与手机首屏空间占用。
浏览器环境使用临时 SQLite、真实本地 API/Worker 与 demo 流程；失败诊断使用拦截响应的契约样例。
本轮结果属于前端本地回归，不覆盖真实模型效果、外部服务和生产部署验收。

## 页面预览

- [桌面新建分析](images/workbench-create.png)
- [桌面报告](images/workbench-report.png)
- [完整报告长图](images/commerce-analysis-desktop.png)
- [手机新建分析](images/workbench-create-mobile.png)
- [手机报告](images/commerce-analysis-mobile.png)
- [桌面登录](images/workbench-login.png)
- [手机登录](images/workbench-login-mobile.png)
