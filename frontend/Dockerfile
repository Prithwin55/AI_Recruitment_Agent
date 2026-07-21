# Builds the frontend and bakes it into a Caddy image that also reverse-proxies the two APIs.
# Build from the REPO ROOT (needs the Caddyfile at repo root):
#   docker build -f frontend/Dockerfile -t airecruit-web .
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
