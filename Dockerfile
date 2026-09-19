# syntax=docker/dockerfile:1
FROM oven/bun:1.3.1 AS bun
FROM node:22-bookworm-slim AS frontend
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY .build/frontend/ ./
COPY scripts/patch_frontend.py /tmp/patch_frontend.py
RUN python3 /tmp/patch_frontend.py /src
ENV HUSKY=0 CI=1 VITE_API_BASE_URL="" VITE_API_PREFIX=""
RUN bun install --frozen-lockfile --ignore-scripts
RUN cd apps/admin && bun run build
RUN cd apps/user && bun run build

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends nginx python3 python3-yaml ca-certificates tzdata tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --chmod=0755 .build/backend/ppanel-server /app/modules/ppanel-server
COPY .build/backend/LICENSE /app/licenses/backend-LICENSE
COPY --from=frontend /src/LICENSE /app/licenses/frontend-LICENSE
COPY --from=frontend /src/apps/admin/dist/ /app/web/admin/
COPY --from=frontend /src/apps/user/dist/ /app/web/user/
COPY .build/manifest.json /app/build-manifest.json
COPY runtime/ /app/runtime/
RUN mkdir -p /app/etc /run/ppanel /app/logs && touch /run/ppanel/subscribe.conf && nginx -t -c /app/runtime/nginx.conf
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=10s --start-period=90s --retries=3 CMD ["python3", "/app/runtime/healthcheck.py"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/app/runtime/entrypoint.py"]
