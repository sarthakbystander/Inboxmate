# InboxMate

Your inbox, without the noise.

An open-source, private, self-hosted email assistant that helps you understand,
organize, and respond to email. InboxMate is a single, lightweight FastAPI
application with a SQLite database and an optional local or API-based AI model.

No cloud dependence. No telemetry. No giant JavaScript bundles. A small,
serious, self-hosted email product.

---

## Highlights

- **Private by default** — runs entirely on your own hardware. Your messages
  and credentials never leave your machine unless *you* point the AI at a
  remote provider.
- **Lightweight** — FastAPI + Jinja2 + vanilla JS + HTMX, one SQLite file, no
  Redis/Celery/Postgres/Kubernetes/microservices.
- **AI when you want it** — summarize, classify, and draft replies through a
  provider abstraction supporting **OpenAI-compatible APIs** and **Ollama**.
- **Safe by design** — inbox data is untrusted; HTML is sanitized, prompts are
  fixed server-side, drafts are never auto-sent, and every state-changing
  request is CSRF-protected.

---

## Table of contents

- [Architecture](#architecture)
- [Installation](#installation)
- [Local development](#local-development)
- [Environment variables](#environment-variables)
- [Database setup](#database-setup)
- [Email configuration](#email-configuration)
- [AI provider configuration](#ai-provider-configuration)
- [Ollama setup](#ollama-setup)
- [Docker deployment](#docker-deployment)
- [Security considerations](#security-considerations)
- [Testing](#testing)
- [Project structure](#project-structure)

---

## Architecture

```text
Browser
HTML + CSS + JS + HTMX
          │
          ▼
       FastAPI
          │
   ┌──────┼────────┐
   ▼      ▼        ▼
 Auth   Email      AI
        Service   Service
   │      │        │
   │      ▼        ▼
   │   IMAP/SMTP  AI API
   │              / Ollama
   │
   └────────┬──────────┐
            ▼          ▼
          SQLite    Encrypted
                    Secrets
```

Everything lives inside one application. The only external services are the
mail server (IMAP/SMTP) and, optionally, an AI provider.

---

## Installation

The recommended way to deploy is Docker Compose (see
[Docker deployment](#docker-deployment)). For local development:

```bash
git clone https://github.com/sarthakbystander/Inboxmate.git
cd Inboxmate

# 1. Python 3.11+ with a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the app
cp .env.example .env
#   ... edit .env, at minimum set INBOXMATE_SECRET_KEY

# 4. Run
python -m app        # http://localhost:8080
```

The database and encryption key are created automatically in `data/` on first
run.

---

## Local development

```bash
# Start the dev server with auto-reload
uvicorn app.main:app --reload --port 8080

# Run the test suite
python -m pytest
```

On the homepage, click **Get Started** to register an account. Then, in
**Settings**, either configure your real IMAP/SMTP credentials or use the
**"Load demo inbox"** button on the empty inbox page to populate it with
sample messages from the built-in mock backend — no network or credentials
required.

### Mock backends

The external integrations (IMAP and the AI provider) are isolated behind
service interfaces (`MailBackend`, `LLMProvider`). This lets the application
and its tests run end-to-end without touching the network:

- `MockMailBackend` — a working in-memory IMAP inbox. Activate it with the
  `X-InboxMate-Mock: 1` request header on `/inbox/sync`.
- `_DefaultProvider` for the AI service — returns a clear error until a real
  provider is configured; `FakeProvider` in tests captures prompts.

Real integrations are never faked as "done" — they are cleanly implementable
and verified against live servers in production, while the app always has a
testable path locally.

---

## Database setup

SQLite, zero configuration. The schema is created idempotently on startup.

- Database file: `$INBOXMATE_DATA_DIR/inboxmate.db` (default `./data/`).
- WAL mode + foreign keys are enabled for crash-safety and integrity.
- Tables: `users`, `sessions`, `settings`, `folders`, `emails`,
  `attachments`, `drafts`, `audit_log`.
- Sensitive columns (IMAP/SMTP passwords, AI API keys) are stored encrypted
  (AES-256 via Fernet). See [Security](#security-considerations).

Back up the `data/` directory (or just the `.db` file) to preserve accounts,
messages, and drafts. **Keep a separate copy of `secret.key`** — without it,
stored credentials cannot be decrypted.

---

## Environment variables

All configuration is via environment variables (loaded from `.env` when
present). See [`.env.example`](.env.example) for the full list and
descriptions. Key variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `INBOXMATE_SECRET_KEY` | dev-only | Session/encryption secret **must set** |
| `INBOXMATE_DATA_DIR` | `./data` | Data + encryption key location |
| `INBOXMATE_BASE_URL` | `http://localhost:8080` | Public URL (secure cookies) |
| `INBOXMATE_SESSION_HOURS` | `168` | Session lifetime in hours |
| `INBOXMATE_LOGIN_RATE_LIMIT` | `10` | Login/register /IP/min |
| `INBOXMATE_MAX_EMAIL_SIZE` | `3145728` | Max stored email size (bytes) |
| `INBOXMATE_MAX_ATTACHMENT_SIZE` | `10485760` | Max attachment size (bytes) |
| `INBOXMATE_PAGE_SIZE` | `50` | Emails per inbox page |
| `INBOXMATE_AI_PROVIDER` | `none` | `none` \| `openai` \| `ollama` |
| `INBOXMATE_AI_BASE_URL` | — | OpenAI-compatible base URL |
| `INBOXMATE_OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `INBOXMATE_ENCRYPTION_KEY` | auto | Fernet key derivation secret |

Generate a strong secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Email configuration

Under **Settings → Email**, provide:

- **IMAP host/port** and **SMTP host/port** (defaults: 993 / 587).
- **Email address** and **Display name**.
- **IMAP/SMTP usernames and passwords**. Supplied passwords are encrypted at
  rest; leave a field blank to keep the previously saved value.

Security notes:

- Credentials are decrypted only at the moment of use (syncing/sending) and
  are **never** sent to the browser or rendered back into the page.
- The **Sync** action in the inbox runs a real IMAP `FETCH` when a mailbox is
  configured. With no mailbox configured, use the demo/inbox path for testing.

### Common providers

| Provider | IMAP host | IMAP SSL | SMTP host | SMTP port | SMTP TLS |
| --- | --- | --- | --- | --- | --- |
| Gmail | `imap.gmail.com` | on (993) | `smtp.gmail.com` | 587 | on |
| Outlook | `outlook.office365.com` | on (993) | `smtp-mail.outlook.com` | 587 | on |
| Yahoo | `imap.mail.yahoo.com` | on (993) | `smtp.mail.yahoo.com` | 465 | off (SSL) |

Many providers require an **app password** rather than your account password.

---

## AI provider configuration

Under **Settings → AI provider**, choose a provider and model. The rest of
InboxMate is agnostic to the provider — the `AIService` abstraction routes to
`OpenAICompatibleProvider` or `OllamaProvider`.

### OpenAI-compatible APIs

Any vendor exposing the OpenAI `/chat/completions` shape (OpenAI, OpenRouter,
Groq, Together, a local vLLM/LM Studio proxy, …):

- Provider: **OpenAI-compatible API**
- Base URL: e.g. `https://api.openai.com/v1`
- API key: your key (encrypted at rest) — can also be set via
  `INBOXMATE_AI_API_KEY` at deploy time
- Model: e.g. `gpt-4o-mini`

### Ollama

- Provider: **Ollama**
- Base URL: the default `http://localhost:11434` usually works; from Docker
  use `http://host.docker.internal:11434` if Ollama runs on the host.
- Model: e.g. `llama3.2:3b`

Use **Test connection** to verify the model answers before saving your key.

---

## Ollama setup

On the host (any OS):

```bash
# Install: https://ollama.com/download
ollama pull llama3.2:3b
ollama serve
```

Then point InboxMate at it (Settings → AI provider → Ollama). If you run
InboxMate in Docker, either run Ollama as a compose service
(`docker compose --profile llm up`) and set the base URL to the service name,
or use `extra_hosts` (already present in `docker-compose.yml`) and set the
base URL to `http://host.docker.internal:11434`.

---

## Docker deployment

### 1. Configure

```bash
cp .env.example .env
# set INBOXMATE_SECRET_KEY to a long random value
# set INBOXMATE_BASE_URL to your public https:// domain (or http://IP)
```

### 2. Run

```bash
docker compose up --build -d
```

This starts:

- **inboxmate** — the application (single uvicorn worker, low memory).
- **caddy** — reverse proxy that terminates TLS automatically (needs your
  domain to resolve to the host and ports 80/443 open).
- **ollama** *(only with `--profile llm`)* — local LLM server.

### 3. Update the Caddyfile

Edit [`Caddyfile`](Caddyfile) and replace `mail.example.com` with your domain.
Caddy obtains a Let's Encrypt certificate automatically. For a bare-IP or
localhost deployment, change the site address to `http://localhost` (plain
HTTP, no certificate).

Volumes persist the database, encryption key, and Caddy certs across restarts.

---

## Security considerations

Security is a first-class feature. Highlights:

- **Passwords** — Argon2id hashing (`argon2-cffi`).
- **Sessions** — opaque random tokens stored server-side in SQLite; cookies are
  `HttpOnly`, `SameSite=Lax`, and (behind HTTPS) `Secure`. Sessions expire.
- **CSRF** — every state-changing request requires a per-session token, either
  in the `_csrf` form field or the `X-CSRF-Token` header (injected by a tiny
  script for HTMX requests). Constant-time comparison.
- **Encryption at rest** — IMAP/SMTP passwords and AI API keys are encrypted
  with Fernet (AES-128-CBC + HMAC). The key is derived from
  `INBOXMATE_ENCRYPTION_KEY` or stored in `data/secret.key` with `0600`
  permissions.
- **Email is untrusted** —
  - HTML bodies are sanitized with **nh3** (scripts, event handlers and
    `javascript:` URLs stripped; links get `rel="noopener noreferrer"`).
  - Email content never executes scripts; images are not auto-proxied.
  - Attachment downloads are content-disposition-ed, size-limited, and
    IDOR-guarded (both email and attachment ids must belong to the user).
  - **AI prompts** use fixed, server-side system instructions and receive only
    the minimum content needed. Instructions inside an email can **never**
    change InboxMate's behavior, permissions, or access.
- **No credentials in logs** — connection errors are logged at a coarse level
  without passwords; secrets are never echoed.
- **No API keys in frontend JS** — keys are decrypted server-side only.
- **No secrets committed** — `data/`, `.env`, `*.db`, and `secret.key` are
  git-ignored; only `.env.example` (names, no values) is tracked.
- **Authorization / IDOR** — every user-owned resource query filters by the
  authenticated user id; cross-user access returns 404.
- **Input validation & length limits** — email addresses validated, subjects
  sanitized, message/attachment size caps enforced.
- **Rate limiting** — authentication endpoints are limited per IP/minute (an
  in-process limiter with no external infrastructure).
- **Audit log** — register/login/logout/sync/send/delete actions are recorded
  per user in `audit_log`.
- **Minimum privilege** — the app runs as a non-root user in Docker, opens no
  extra ports, and exposes no admin/debug routes (`docs`, `redoc`, `openapi`
  are disabled).

### Threat model notes

- The AI is **not** an agent: it has no tools and no access to the database,
  credentials, filesystem, shell, or network beyond the configured provider
  endpoint. Its output is only ever displayed or stored as draft text.
- Emails are never auto-sent. Drafting is separate from sending; sending is an
  explicit, authenticated, CSRF-protected action.
- Use HTTPS in front of InboxMate (Caddy does this for you) so session cookies
  and form submissions are not exposed in transit.

---

## Testing

```bash
python -m pytest
```

The suite covers the security-sensitive surface: Argon2 hashing,
password/session flows, CSRF enforcement, IDOR guards, encryption at rest,
HTML sanitization, rate limiting, the AI abstraction (prompt isolation and
label validation), email parsing/attachment handling, settings persistence,
and the scheduler.

---

## Project structure

```text
inboxmate/
├── app/
│   ├── main.py             # FastAPI wiring
│   ├── config.py           # Environment-driven settings
│   ├── templating.py       # Jinja2 environment
│   ├── routes/
│   │   ├── auth.py         # register / login / logout
│   │   ├── inbox.py        # sync + inbox listing / search
│   │   ├── email.py        # email view + AI actions + attachments
│   │   ├── drafts.py       # compose / edit / send / delete
│   │   └── settings.py     # email + AI + account settings
│   ├── services/
│   │   ├── mail.py         # IMAP/SMTP backend + mock
│   │   ├── ai.py           # AIService + providers
│   │   ├── classifier.py   # classification wrapper
│   │   ├── scheduler.py    # in-process periodic jobs
│   │   ├── sanitize.py     # HTML sanitization + validation
│   │   └── factory.py      # settings -> service builders
│   ├── security/
│   │   ├── auth.py         # Argon2, sessions, current-user
│   │   ├── encryption.py   # AES-256-Fernet at rest
│   │   ├── csrf.py         # CSRF helpers
│   │   └── ratelimit.py    # in-process limiter
│   ├── database/
│   │   ├── db.py           # SQLite connection + schema
│   │   └── models.py       # parameterized queries
│   ├── templates/          # Jinja2 templates (incl. partials/)
│   └── static/             # CSS + vendored HTMX + favicon
├── data/                   # SQLite DB + encryption key (git-ignored)
├── tests/                  # pytest suite
├── Dockerfile
├── docker-compose.yml
├── Caddyfile
├── .env.example
├── requirements.txt
└── README.md
```

---

## License

[MIT](LICENSE). InboxMate — self-hosted, private, open source.
