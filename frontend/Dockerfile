# Frontend image: build the Vite bundle, then serve it with nginx, which also
# proxies /api and /ws to the backend container (same-origin, so no CORS).
# Build context is the REPO ROOT (set by the compose file).

FROM node:22-alpine AS build
WORKDIR /app/frontend
# Lockfile-first for cached installs.
COPY frontend/package.json frontend/package-lock.json ./
# `npm install`, not `npm ci`: the lockfile is generated on the host (Windows),
# and Vite 8's Rolldown ships platform-specific native bindings. `npm ci`
# demands an exact per-platform match and fails cross-platform; `npm install`
# resolves the right linux-musl binding at build time.
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
