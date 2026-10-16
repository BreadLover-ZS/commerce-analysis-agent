FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build
FROM nginxinc/nginx-unprivileged:1.28-alpine
ENV TRUSTED_INGRESS_IP=172.30.0.11 NGINX_ENVSUBST_FILTER=^TRUSTED_INGRESS_IP$
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
