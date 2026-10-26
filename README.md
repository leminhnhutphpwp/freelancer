# Freelancer Assistant

Personal Freelancer.com automation backend (Python / FastAPI). It connects your
Freelancer account with a **Personal Access Token**, lets you manage your
professional profile and portfolio, discovers matching projects, and lets you
**approve every outbound action (bid / award response / message) from Telegram**
before anything is sent to Freelancer.

> ⚠️ **Read-only by default.** No bid, award response, or message is ever sent
> without an explicit confirmation (a Telegram button or a REST confirm call).

---

## Features

| Area | What it does |
|------|--------------|
| **Auth** | Validates your PAT via `GET /users/0.1/self/`; caches account identity. |
| **Profile / portfolio** | Read portfolio; create/update professional profile (tagline, rate, description, skills); manage your skill/job list. |
| **Project discovery** | Configurable search rules (keywords, skills, budget, project type, bid caps, age). Server-side filters + client-side scoring. |
| **Dedupe** | A `(project_id, rule_id)` unique constraint guarantees a project is notified only once, even across worker restarts. |
| **Bids** | Draft a bid from a project card, edit amount/period/proposal, then confirm. |
| **Awards** | When a bid is awarded, Telegram offers Accept/Deny — each with its own confirm step. |
| **Messaging** | Syncs chat threads; new inbound messages are forwarded to Telegram with a Reply flow (draft → confirm → send). |
| **Telegram** | Long-polling bot for commands and inline confirm buttons. |
| **Worker** | Periodic rule scans, award polling, thread sync, action expiry. PG advisory locks make multiple replicas safe. |

---

## Architecture

```
┌────────────┐   ┌────────────┐   ┌────────────┐
│   api      │   │   worker   │   │    bot     │   (3 processes)
│  FastAPI   │   │  pollers   │   │ PTB polling│
└─────┬──────┘   └─────┬──────┘   └─────┬──────┘
      │                │                │
      └────────────────┴───────┬────────┘
                               ▼
                     ┌──────────────────┐
                     │   PostgreSQL     │
                     │  (SQLAlchemy)    │
                     └──────────────────┘
```

- **api** — REST admin API (OpenAPI at `/api/v1/docs`), protected by `X-API-Key`.
- **worker** — `python -m app.worker`: scans rules, polls awards/threads, expires stale confirmations.
- **bot** — `python -m app.telegram.bot`: Telegram long polling for commands + confirm buttons.
- **Freelancer.com** — accessed via `app/freelancer/client.py` (httpx, `Freelancer-OAuth-V1` header).

Every outbound write goes through the `pending_actions` table with a
single-use confirmation token:

```
draft → pending_confirmation → executing → succeeded | failed
                                    └── expired | cancelled
```

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (or pip)
- PostgreSQL 14+ (or SQLite for local dev/tests)
- A Freelancer.com account and a **Personal Access Token**
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))

---

## Setup

### 1. Get a Freelancer Personal Access Token

1. Log in to Freelancer.com.
2. Go to **Settings → API** (or the developer portal) and create a Personal Access Token.
3. Copy the token. **Treat it like a password** — never commit it.

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather), `/newbot`, follow the prompts.
2. Copy the bot token.
3. Find your numeric chat id (e.g. via `@userinfobot`) and add it to `TELEGRAM_ALLOWED_CHAT_IDS`.

### 3. Configure

```bash
cp .env.example .env
# edit .env
```

Key variables:

| Variable | Description |
|----------|-------------|
| `FREELANCER_ACCESS_TOKEN` | Your Freelancer PAT. |
| `FREELANCER_BASE_URL` | `https://www.freelancer.com/api` (or sandbox). |
| `DATABASE_URL` | `postgresql+asyncpg://...` (or `sqlite+aiosqlite:///./data.db` for local). |
| `ADMIN_API_KEY` | Long random string for the REST API (`openssl rand -hex 32`). |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Comma-separated numeric chat ids allowed to command the bot. |

### 4. Install & run

```bash
uv sync --extra dev          # install deps
uv run alembic upgrade head  # create schema
uv run uvicorn app.main:app --port 8000   # API
uv run python -m app.worker               # worker (separate terminal)
uv run python -m app.telegram.bot         # bot (separate terminal)
```

### 5. Docker Compose (recommended)

```bash
cp .env.example .env   # fill in secrets
docker compose up --build
```

This starts `db`, `api`, `worker`, and `bot`. The API runs `alembic upgrade head`
on startup.

---

## Usage

### Connect your account

```bash
curl -X POST http://localhost:8001/api/v1/account/connect \
  -H "x-api-key: $ADMIN_API_KEY"
```

### Create a search rule

```bash
curl -X POST http://localhost:8001/api/v1/rules \
  -H "x-api-key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "python backend",
    "filters": {"query": "python", "project_types": ["fixed"]},
    "match": {"must_keywords": ["fastapi"], "preferred_keywords": ["backend", "postgresql"]},
    "bid_template": {"amount": 150, "period_days": 7},
    "min_score": 1.0
  }'
```

### Test a rule without notifying

```bash
curl -X POST http://localhost:8001/api/v1/rules/1/test -H "x-api-key: $ADMIN_API_KEY"
```

### Telegram

Once the bot is running and your chat id is allowed:

- `/status` — connection + pending drafts
- `/rules` — list search rules
- `/pending` — drafts awaiting confirmation
- `/threads` — recent Freelancer chats
- Project cards arrive automatically with **Chi tiết / Tạo bid / Bỏ qua** buttons.
- Every write shows a preview with **✅ Confirm / ✖️ Cancel**.

---

## REST API overview

All routes under `/api/v1` require `X-API-Key: <ADMIN_API_KEY>`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/account/connect` | Validate PAT + cache identity |
| GET | `/account/status` | Connection status |
| GET | `/account/portfolio` | List portfolio |
| GET/POST/PUT | `/account/profiles` | Read/create/update professional profile |
| GET/PUT/POST/POST | `/account/skills` | Read/set/add/remove skills |
| GET/POST/PATCH/DELETE | `/rules` | Search rule CRUD |
| POST | `/rules/{id}/test` | Dry-run scan (no notify) |
| POST | `/rules/{id}/scan` | Force scan + notify |
| GET | `/projects`, `/projects/matches` | Cached projects / matches |
| GET | `/projects/{id}?live=true` | Project detail (live fetch) |
| POST | `/actions/bid-draft` | Create a bid draft |
| POST | `/actions/message-draft` | Create a message draft |
| POST | `/actions/{token}/confirm` | Confirm + execute |
| POST | `/actions/{token}/cancel` | Cancel a draft |
| GET | `/actions`, `/actions/audit` | History + audit log |
| GET | `/threads`, `/threads/{id}/messages` | Chat threads |
| GET | `/health/live`, `/health/ready` | Health checks |

Interactive docs: `http://localhost:8001/api/v1/docs`.

---

## Tests

```bash
uv run pytest
uv run ruff check app tests
```

Tests use an in-memory SQLite database and mocked HTTP (respx). No real
Freelancer or Telegram calls are made.

---

## Security notes

- The Freelancer PAT and Telegram token are read from environment variables and
  are **redacted from all logs** (`app/log.py`).
- The REST API is protected by a constant-time `X-API-Key` check. In production
  the app refuses to start without `ADMIN_API_KEY`.
- Telegram only accepts commands from `TELEGRAM_ALLOWED_CHAT_IDS`.
- Confirmation tokens are single-use (atomic conditional UPDATE) and expire
  after `PENDING_ACTION_TTL_SECONDS`.
- No outbound write happens without an explicit confirm.

---

## Known limitations

- **Portfolio is read-only** — the public API only exposes listing portfolios;
  there is no endpoint to edit portfolio items.
- **One account** — designed for a single Freelancer account via PAT. Multi-user
  OAuth2 (authorization-code flow + refresh tokens) is a future extension.
- **"Accept job" means bid → award → accept**, matching Freelancer's actual
  workflow. The bot never auto-bids or auto-accepts.
- The Telegram reply-composition state (`AWAITING_REPLY`) is in-memory and
  resets on bot restart; no Freelancer write is lost because nothing is sent
  until you confirm.

---

## Production deployment

The app deploys to a single server via SSH + rsync + Docker Compose, fronted by
nginx (Let's Encrypt SSL). Set `DEPLOY_HOST` (e.g. `root@your-server`) and
`DEPLOY_DIR` (default `/srv/freelancer`) for `scripts/deploy-remote.sh`, and replace
`freelancer.example.com` with your domain in `deploy/freelancer.example.conf`.

### Layout

```
/srv/freelancer/   synced source + docker-compose.yml + .env
  .env                            production secrets (pushed from .env.production)
  deploy/freelancer.example.conf   nginx vhost (installed to /etc/nginx/sites-available/)
```

- `db` — PostgreSQL 16 (volume `pgdata`)
- `api` — FastAPI on container port 8000, mapped to host `127.0.0.1:8001`
- `worker` — background pollers
- `bot` — Telegram long-polling bot

nginx proxies `freelancer.example.com` → `127.0.0.1:8001`.

### One-shot deploy

```bash
# 1. Prepare production env (secrets, never committed):
cp .env.example .env.production   # fill in real values, set ENVIRONMENT=production,
                                  # DATABASE_URL=postgresql+asyncpg://freelancer:freelancer@db:5432/freelancer_assistant

# 2. Deploy (sync source + .env, build, start, health check):
scripts/deploy-remote.sh

# 3. First time only — install nginx vhost + Let's Encrypt SSL:
scripts/deploy-remote.sh nginx
```

### Deploy script actions

| Action | Purpose |
|--------|---------|
| `deploy` (default) | sync source + `.env`, `docker compose up --build -d`, health check |
| `sync` | rsync source + push `.env` only |
| `up` | build + start containers on server |
| `restart [svc]` | restart services |
| `logs [svc]` | tail logs |
| `ps` | show service status |
| `down` | stop and remove containers |
| `status` | API health + account status |
| `nginx` | install nginx vhost + request SSL cert |

Override the target with env vars: `DEPLOY_HOST`, `DEPLOY_DIR`, `DEPLOY_ENV`,
`API_PORT`.

### Manual nginx setup (if not using `deploy-remote.sh nginx`)

```bash
scp deploy/freelancer.example.conf root@your-server:/etc/nginx/sites-available/
ssh root@your-server 'ln -s /etc/nginx/sites-available/freelancer.example.com /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx'
ssh root@your-server 'certbot --nginx -d freelancer.example.com'
```

---

## Scripts

A set of helper scripts under `scripts/` for dev, deploy and operations:

| Script | Purpose |
|--------|---------|
| `scripts/check.sh` | Check environment prerequisites + config status |
| `scripts/dev.sh [api\|worker\|bot\|all]` | Run the local dev stack with uv |
| `scripts/test.sh` | Run lint + format + pytest |
| `scripts/migrate.sh upgrade\|downgrade\|revision\|current\|history` | Alembic migrations |
| `scripts/smoke.sh` | **Read-only** Freelancer connection smoke test (safe) |
| `scripts/status.sh [base_url]` | API health + account status + rules |
| `scripts/rule.sh list\|create\|test\|scan\|delete` | Manage search rules via REST |
| `scripts/deploy.sh up\|build\|restart\|logs\|down\|ps` | Docker Compose lifecycle (local) |
| `scripts/deploy-remote.sh [action]` | Deploy to the production server (see above) |
| `scripts/backup.sh [dir]` | Dump PostgreSQL to a timestamped `.sql.gz` |
| `scripts/clean.sh` | Remove caches/build artifacts (keeps `.env`, `backups/`) |

All scripts read `.env` (without overriding already-exported variables) and
fail with a clear message when a required secret is missing.

---

## Project layout

```
app/
  api/            FastAPI routes + security
  db/             SQLAlchemy models + session
  freelancer/     Freelancer.com REST client
  services/       matching, search, bids, threads, actions, notify, render
  telegram/       PTB bot (commands + callbacks)
  worker/         background pollers
  config.py       settings
  main.py         FastAPI app factory
migrations/       Alembic migrations
tests/            unit / contract / integration
```
## License

[MIT](LICENSE)
