# 电商运营分析助手前端

React/TypeScript/Vite，展示任务、复核、执行事件、依据和报告下载。
API通过同源 `/api` 访问；开发代理默认 localhost:8000，部署由Nginx转发。
当前界面使用事件游标轮询，后端另提供SSE接口。

```bash
pnpm install --frozen-lockfile
pnpm dev
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

浏览器测试自动启动临时API/Worker与前端，数据独立。若使用系统Chrome：

```bash
COMMERCE_BROWSER_CHANNEL=chrome pnpm test:e2e
```

配置开发代理用VITE_API_PROXY_TARGET；VITE_API_BASE_URL默认空字符串，优先使用同源部署。
不支持依赖跨站Cookie的随意前后端域名组合。业务与验收边界见根README和docs。
