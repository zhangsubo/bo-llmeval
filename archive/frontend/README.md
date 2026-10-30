# EvalPlat 管理台

这是 LLM API 服务评测管理平台的 React 管理台，使用 React 19、TypeScript、Vite 和 Ant Design。

## 本地开发

```bash
npm ci
npm run dev
```

Vite 默认监听 `5173`，并把 `/api` 请求代理到 `http://localhost:8000`。可通过 `EVALPLAT_API_TARGET` 覆盖 API 地址。

## 生产构建

```bash
npm run build
```

生产部署使用仓库根目录的 `Dockerfile.frontend`。Nginx 托管 `dist` 并将 `/api`、`/healthz` 和 `/readyz` 反代到后端，因此浏览器保持同源访问，不需要额外 CORS 配置。Compose 暴露端口为 `8080`。

前端假设后端已经完成数据库迁移并可通过健康探针访问。生产环境请通过同源反向代理暴露前端，不要直接将后端端口暴露到公网。
