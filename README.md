# Extralessons

A booking system for school extra-curricular activities. The school publishes a public catalogue of classes for the term; parents create an account, add their children, and request places. The school office reviews every request, manages waiting lists, and hand-picks who gets a freed seat. Providers (coaches, tutors) run their classes from their own dashboard. Families are kept in the loop by email and WhatsApp.

## Features

**Public**
- Browse the catalogue of published classes for the active term (age range, schedule, provider, seats) without an account.

**Parents**
- Self-service signup (can be switched off by the school), add children with date of birth and notes for providers.
- Multi-guardian support: invite a co-parent by email to share access to a child.
- Request a place in a class; age and duplicate checks are enforced automatically.
- Confirm or decline waiting-list offers before they expire; withdraw from a class at any time.
- Per-user notification preferences (email and/or WhatsApp).

**Providers**
- Dashboard with class rosters for their own classes.
- Per-session attendance taking.
- Message the families of their classes (announcements/broadcasts).

**School admin**
- Review queue: approve or reject enrollment requests (approve enrolls directly if a seat is free, otherwise waitlists).
- When a seat frees up, hand-pick which waitlisted family gets the offer; offers expire automatically after a configurable number of hours (default 48).
- Optional email alerts on new requests and freed seats.
- Manage terms, classes, providers, notification templates, and site-wide settings in the Django admin; admin tools dashboard at `/admin-tools/`.

## Architecture at a glance

Django 5 + PostgreSQL, server-rendered templates progressively enhanced with HTMX (vendored, no JS build step). Static files via WhiteNoise, media served by Caddy in production.

Docker Compose services:

| Service    | Role                                                                 |
|------------|----------------------------------------------------------------------|
| `db`       | PostgreSQL 16                                                        |
| `web`      | Django under gunicorn (dev override: `runserver` with autoreload)    |
| `notifier` | `python manage.py run_notifier` — notification delivery worker       |
| `caddy`    | Reverse proxy + automatic TLS + media file serving (disabled in dev) |

**Transactional outbox.** State changes never talk to SMTP or the WhatsApp API directly. Instead, `Notification` rows are queued inside the same database transaction as the state change (`apps/notifications/services.py`), so they commit atomically with it. The `notifier` service (`apps/notifications/worker.py`) claims batches with `SELECT ... FOR UPDATE SKIP LOCKED`, sends outside any transaction, and retries failures with exponential backoff up to `NOTIFIER_MAX_ATTEMPTS`. It also expires overdue waiting-list offers each cycle. Every row is a permanent delivery log, inspectable in the admin.

**Enrollment state machine.** All transitions go through `apps/enrollments/services.py`, which takes a row lock on the class as a capacity mutex so a class can never be oversubscribed under concurrent requests.

```
parent registers ──► REQUESTED ── admin approves ──► ENROLLED   (seat free)
                         │                       └─► WAITLISTED (class full)
                         └── admin rejects ──────► CANCELLED

WAITLISTED ── admin offers seat ──► OFFERED ── parent confirms ──► ENROLLED
OFFERED ── parent declines / offer expires (48h, configurable) ──► CANCELLED

any active state ── withdrawal / admin cancel / class cancelled ──► CANCELLED
```

`ENROLLED` and `OFFERED` hold a seat; an offer reserves the seat until confirmed, declined, or expired.

## Local development

Prerequisites: Docker Desktop and VS Code.

**Quickstart:** open the folder in VS Code, then *Terminal → Run Task → "Start dev environment"*. Or from a terminal:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

This boots Postgres, the Django dev server (autoreload, console email, stub WhatsApp), and the notifier worker. Caddy is not started in dev. Then seed demo data (task *"Seed demo data"*, or):

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web python manage.py seed_demo
```

The app is at http://localhost:8000 and the Django admin at http://localhost:8000/admin/. Demo accounts (all with password `demo1234`):

| Account                | Role                                    |
|------------------------|-----------------------------------------|
| `admin@school.test`    | School admin (staff + superuser)        |
| `coach@provider.test`  | Provider — AllStars Sports              |
| `tutor@provider.test`  | Provider — Bright Minds                 |
| `parent1@family.test`  | Parent with 2 children                  |
| `parent2@family.test`  | Parent with 1 child                     |

Seeding is idempotent and also creates a demo term and four sample classes.

Other VS Code tasks: *Run tests*, *Create superuser*, *Make migrations*, *Django shell*, *Tail logs*, *Stop dev environment*.

**Debugging:** start the stack with `DEBUGPY=1` (e.g. `DEBUGPY=1 docker compose -f docker-compose.yml -f docker-compose.dev.yml up`), then use the *"Attach to Django (docker, DEBUGPY=1)"* launch configuration to attach on port 5678.

**Tests** run with pytest inside the web container and need Postgres (the test settings point at the same database server):

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm web pytest
```

## Configuration

### Environment variables (`.env`)

Copy `.env.example` to `.env`. Docker Compose reads it for the `web` and `notifier` services.

| Variable | Purpose |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` in production (dev override sets `config.settings.dev`) |
| `SECRET_KEY` | Django secret key — set to a long random string |
| `DEBUG` | Keep `false` outside development |
| `ALLOWED_HOSTS` | Comma-separated hostnames the app serves |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins, e.g. `https://activities.example.com` |
| `SITE_ADDRESS` | Address Caddy serves (drives automatic TLS) |
| `SITE_URL` | Absolute base URL used in notification links (default `http://localhost:8000`) |
| `TIME_ZONE` | Default `Europe/Malta` |
| `POSTGRES_PASSWORD` | Used by the `db` container and the app's connection string |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` | SMTP settings for outgoing email |
| `DEFAULT_FROM_EMAIL` | From address, e.g. `School Activities <notifications@example.com>` |
| `WHATSAPP_ENABLED` | `false` = log WhatsApp messages instead of sending (stub) |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_API_VERSION` | Meta WhatsApp Cloud API credentials |
| `NOTIFIER_BATCH_SIZE` | Notifications delivered per worker cycle (default 20) |
| `NOTIFIER_MAX_ATTEMPTS` | Retries before a notification is marked failed (default 5) |

### Runtime configuration (Django admin)

Most day-to-day settings are editable in the admin without redeploying, and both are seeded with sensible defaults:

- **Site configuration** (singleton): school name, contact email, catalogue intro text, whether parent self-signup is open, waiting-list offer expiry in hours, and toggles for the admin alert emails (new request, seat freed).
- **Notification templates** (one row per event): email subject/body as Django template strings (context includes `school_name`, `parent_name`, `child_name`, `class_title`, `schedule`, `action_url`, `offer_expires_at`, ...), an enabled flag, plus the WhatsApp mapping — approved template name, language, and which context keys fill the `{{1}}..{{n}}` placeholders. Leave the WhatsApp template name empty to skip WhatsApp for that event.

## WhatsApp setup

WhatsApp delivery uses the Meta WhatsApp Cloud API and sends business-initiated *template* messages only. You need:

1. A Meta WhatsApp Business account with a registered phone number — note the **phone number ID** and create a permanent **access token**.
2. Message templates created and **pre-approved in Meta Business Manager**, one per notification event you want on WhatsApp.
3. Fill each approved template's name (and language/parameter order) into the matching **Notification template** row in the Django admin.
4. In `.env`, set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and `WHATSAPP_ENABLED=true`.

With `WHATSAPP_ENABLED=false` (the default, and always in dev settings) a stub adapter logs messages instead of calling Meta; the production settings switch to the real `WhatsAppCloudAdapter` only when the flag is true. Parents opt in per account and must have a phone number in international format.

## Production deployment (VPS)

```sh
cp .env.example .env
# edit .env: SITE_ADDRESS, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, SITE_URL,
# SECRET_KEY, POSTGRES_PASSWORD, SMTP + WhatsApp credentials
docker compose up -d
```

Point your domain's DNS at the server; Caddy obtains and renews TLS certificates automatically for the `SITE_ADDRESS` you set. Migrations run automatically on `web` startup. Create the first admin account:

```sh
docker compose run --rm web python manage.py createsuperuser
```

**Backups:** the database lives in the `pgdata` volume. A nightly `pg_dump` cron is the minimum, e.g.:

```
0 3 * * * cd /path/to/extralessons && docker compose exec -T db pg_dump -U app extralessons | gzip > /var/backups/extralessons-$(date +\%F).sql.gz
```

Also back up the `media` volume (uploaded images).

## Project layout

```
config/
  settings/
    base.py           # shared settings (env-driven)
    dev.py            # DEBUG, console email, stub WhatsApp
    prod.py           # SMTP, real WhatsApp when enabled, security headers
    test.py           # pytest settings (needs Postgres)
  urls.py             # /admin/, /accounts/, /me/, /provider/, /admin-tools/, catalogue at /
apps/
  accounts/           # custom email-login User (roles: ADMIN/PROVIDER/PARENT),
                      # Child, Guardian, GuardianInvite, SiteConfig singleton,
                      # management/commands/seed_demo.py
  catalog/            # Provider, Term, ActivityClass, ClassSession; public catalogue views
  enrollments/        # Enrollment + Attendance models;
                      # services.py = ALL state transitions (register/approve/reject/
                      # offer/confirm/decline/expire/cancel) under a per-class row lock
  notifications/      # NotificationTemplate, Broadcast, Notification (outbox rows);
                      # services.py = queueing/rendering (call inside the state-change
                      # transaction); worker.py = delivery loop (claim, send, retry,
                      # expire offers); channels/ = email + WhatsApp adapters (stub & Meta);
                      # management/commands/run_notifier.py
  dashboards/         # parent, provider and admin-tools views/urls
templates/            # server-rendered HTML (HTMX-enhanced)
static/               # main.css, vendored htmx.min.js
tests/                # pytest suite (services, capacity race, notifications, views)
```
