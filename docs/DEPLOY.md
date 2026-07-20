# Deploying (Docker, single host)

This platform is a **single-server** deployment. `backend` and `ai_service` share **one SQLite DB
and one `storage/` folder**, so they run as two containers on the **same host** mounting the **same
volume**. Each runs as a **single worker** (backend's auto-schedule sweep and ai_service's in-process
interview state must not be duplicated). `web` (Caddy) terminates TLS, serves the built frontend, and
proxies the two APIs. To scale, use a bigger box — not more workers (SQLite + local files are single-node).

```
*.yourco.com ─▶ web (Caddy: TLS + SPA + proxy) ─┬─ /api ─▶ backend :8000 ─┐
   (wildcard DNS)                                └─ /ai  ─▶ ai_service :8100 ┘ share ▶ storage volume
```

## 1. DNS
Point a **wildcard** record at the host: `*.yourco.com` and `yourco.com` → your server IP.
Every tenant subdomain (`acme.yourco.com`) then resolves without per-tenant DNS changes.

## 2. Config
- **Runtime config** lives in `backend/.env` and `ai_service/.env` (loaded via `env_file`). For prod set:
  ```
  ROOT_DOMAIN=yourco.com
  BASE_DOMAIN_SCHEME=https
  FRONTEND_URL=https://yourco.com
  # + real JWT_SECRET, ADMIN_USERNAME/ADMIN_PASSWORD, ANTHROPIC_API_KEY, SMTP_*, etc.
  ```
  Set `ROOT_DOMAIN`/`BASE_DOMAIN_SCHEME` identically in **both** files.
- **Compose + build vars** — set `ROOT_DOMAIN` and `ACME_EMAIL` where compose can see them (repo-root
  `.env` or your shell). `ROOT_DOMAIN` is baked into the SPA at build time (`VITE_ROOT_DOMAIN`) and used
  by the Caddyfile; `ACME_EMAIL` is for Let's Encrypt.

Secrets never enter the images — `.dockerignore` excludes all `.env` files; they're injected at runtime.

## 3. Build & run
```
docker compose up -d --build
```
Brings up `web` (80/443), `backend`, `ai_service`, and creates the shared `storage` volume. The DB is
created + migrated automatically on first backend startup (`init_db` is idempotent).

## 4. First login (super-admin)
Open `https://yourco.com/admin`, sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`, create an
organization (subdomain + a default recruiter email → one-time password), then the recruiter logs in at
`https://<slug>.yourco.com`.

## TLS notes (wildcard subdomains)
The Caddyfile uses **on-demand TLS**: a cert is issued per subdomain on first HTTPS request via the
HTTP-01 challenge — works with the stock `caddy` image, no DNS plugin. The `on_demand_tls.ask` points at
`GET /tenant/allowed`, which returns 200 only for the apex and **real** tenant slugs, so certs are never
minted for arbitrary hostnames.

Prefer a single `*.yourco.com` **wildcard cert** instead? That needs the DNS-01 challenge, which requires
building Caddy with your DNS provider's plugin (e.g. `caddy-dns/cloudflare` via `xcaddy`) and replacing the
`tls { on_demand }` block with:
```
tls {$ACME_EMAIL} { dns cloudflare {env.CF_API_TOKEN} }
```

## Data & backups
Everything lives in the `storage` Docker volume (`app.db` + resumes/transcripts/recordings). Back it up:
```
docker run --rm -v airecruitmentagent_storage:/s -v "$PWD":/b alpine tar czf /b/storage-backup.tgz -C /s .
```
(Volume name is `<project>_storage`; check `docker volume ls`.)

## Migrating existing local data to the server
Copy your dev `storage/app.db` (and the `storage/` files) into the volume before first start, e.g. `docker
cp ./storage/. <backend-container>:/app/storage/`, or restore the backup tarball into the volume. All
pre-existing data belongs to the `default` workspace (reachable at `yourco.com` or `default.yourco.com`).

## Operational reminders
- **Never** set `--workers > 1` or `RELOAD=1` in prod for either service.
- HTTPS is mandatory — camera/mic and the in-browser TTS WASM require a secure context.
- backend + ai_service must stay co-located (shared SQLite volume); they never talk over HTTP.
