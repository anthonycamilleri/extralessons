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

Django 5 + PostgreSQL, server-rendered templates progressively enhanced with
HTMX (vendored, no JS build step). Static files via WhiteNoise, uploaded images
on S3-compatible object storage.

The whole thing runs serverless on Scaleway, as one image in three roles:

| Role | Runs as | Command |
|------|---------|---------|
| `web` | Serverless Container | `gunicorn --config deploy/gunicorn.conf.py config.wsgi` |
| `migrate` | Serverless Job, once per deploy | `manage.py migrate --noinput` |
| `notifier` | Serverless Job, on a cron | `manage.py run_notifier --drain` |

Three roles rather than three images: a migration that ran against code the web
tier does not have is exactly the failure that arrangement avoids. Locally the
same code runs from `docker-compose.yml`, or with no services at all against
SQLite.

Full deployment instructions: [docs/scaleway-setup.md](docs/scaleway-setup.md).

**Transactional outbox.** State changes never talk to SMTP or the WhatsApp API
directly. Instead, `Notification` rows are queued inside the same database
transaction as the state change (`apps/notifications/services.py`), so they
commit atomically with it. The notifier (`apps/notifications/worker.py`) claims
batches with `SELECT ... FOR UPDATE SKIP LOCKED`, sends outside any transaction,
and retries failures with exponential backoff up to `NOTIFIER_MAX_ATTEMPTS`. It
also expires overdue waiting-list offers each cycle. Every row is a permanent
delivery log, inspectable in the admin.

This is also what makes the app cheap to run serverless: there is no queue
broker and no always-on worker. The outbox lives in the database the app
already has, and the notifier is a scheduled job that wakes, drains the queue
and exits — `--drain` keeps it cycling past a single `NOTIFIER_BATCH_SIZE` so a
broadcast to 300 families goes out in one run rather than one batch per tick.

**Enrollment state machine.** All transitions go through
`apps/enrollments/services.py`, which takes a row lock on the class as a
capacity mutex so a class can never be oversubscribed under concurrent
requests. The lock is row-level `SELECT ... FOR UPDATE` inside a transaction,
which survives a transaction-mode connection pooler — unlike advisory locks,
which Serverless SQL Database does not guarantee.

```
parent registers ──► REQUESTED ── admin approves ──► ENROLLED   (seat free)
                         │                       └─► WAITLISTED (class full)
                         └── admin rejects ──────► CANCELLED

WAITLISTED ── admin offers seat ──► OFFERED ── parent confirms ──► ENROLLED
OFFERED ── parent declines / offer expires (48h, configurable) ──► CANCELLED

any active state ── withdrawal / admin cancel / class cancelled ──► CANCELLED
```

`ENROLLED` and `OFFERED` hold a seat; an offer reserves the seat until
confirmed, declined, or expired.

## Local development

Two ways to run it. Pick the first unless you need PostgreSQL.

### Without Docker (SQLite)

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

No services, no containers. The app is at http://localhost:8000 and the admin
at http://localhost:8000/admin/. Notifications queue as usual; deliver them
with `python manage.py run_notifier` in a second terminal (console email in dev
settings, so they print rather than send).

The one thing SQLite cannot do is row-level locking, so
`tests/test_capacity_race.py` skips itself. Anything touching
`apps/enrollments/services.py` needs the PostgreSQL path below before you trust
it.

### With Docker (PostgreSQL)

Open the folder in VS Code and run *Terminal → Run Task → "Start dev
environment"*, or:

```sh
docker compose up --build
docker compose run --rm web python manage.py seed_demo
```

This boots Postgres, the Django dev server (autoreload, console email, stub
WhatsApp) and the notifier as a daemon. Same URLs as above.

Demo accounts (all with password `demo1234`):

| Account                | Role                                    |
|------------------------|-----------------------------------------|
| `admin@school.test`    | School admin (staff + superuser)        |
| `coach@provider.test`  | Provider — AllStars Sports              |
| `tutor@provider.test`  | Provider — Bright Minds                 |
| `parent1@family.test`  | Parent with 2 children                  |
| `parent2@family.test`  | Parent with 1 child                     |

Seeding is idempotent and also creates a demo term and four sample classes.

Other VS Code tasks: *Run tests*, *Create superuser*, *Make migrations*,
*Django shell*, *Tail logs*, *Stop dev environment*.

**Debugging:** start the stack with `DEBUGPY=1`, then use the *"Attach to Django
(docker, DEBUGPY=1)"* launch configuration to attach on port 5678.

### Tests

```sh
pytest                                                    # SQLite, fast
DATABASE_URL=postgres://app:app@localhost:5432/extralessons pytest   # full
```

CI runs both. The PostgreSQL run is the authoritative one.

## Configuration

### Environment variables

Copy `.env.example` to `.env` for local development. In production these are
set on the Serverless Container and Jobs instead of in a file — see
[docs/scaleway-setup.md](docs/scaleway-setup.md).

| Variable | Purpose |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` in production, `config.settings.dev` locally |
| `SECRET_KEY` | Django secret key — set to a long random string |
| `DEBUG` | Keep `false` outside development |
| `ALLOWED_HOSTS` | Comma-separated hostnames the app serves |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins, e.g. `https://activities.example.com` |
| `SITE_URL` | Absolute base URL used in notification links |
| `TIME_ZONE` | Default `Europe/Malta` |
| `LOG_LEVEL` | Root log level; everything goes to stdout |
| `DATABASE_URL` | Unset = SQLite. On Scaleway the username is an IAM application ID and the password its API secret key |
| `DB_POOL` / `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | Client-side connection pool (PostgreSQL only). Raise `DB_POOL_MAX_SIZE` and the container's max-scale together |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` | Object storage for uploaded class images. Unset = local disk, which is ephemeral on serverless |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Object storage credentials |
| `S3_CUSTOM_DOMAIN` | Optional CDN hostname for media URLs |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` | SMTP. Ports 25 and 465 are blocked outbound on Scaleway Serverless; use 587 |
| `DEFAULT_FROM_EMAIL` | From address, e.g. `School Activities <notifications@example.com>` |
| `WHATSAPP_ENABLED` | `false` = log WhatsApp messages instead of sending (stub) |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_API_VERSION` | Meta WhatsApp Cloud API credentials |
| `NOTIFIER_BATCH_SIZE` | Notifications delivered per worker cycle (default 20) |
| `NOTIFIER_MAX_ATTEMPTS` | Retries before a notification is marked failed (default 5) |
| `NOTIFIER_DRAIN_MAX_SECONDS` | Time budget for `run_notifier --drain` (default 300) |

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

## Deployment

Production runs on Scaleway Serverless. Push to `main` and
`.github/workflows/deploy.yml` builds the image for `linux/amd64`, pushes it to
Scaleway Container Registry, runs migrations as a Serverless Job and waits for
them, repoints the notifier job, redeploys the container, then smoke-tests
`/_health`.

Images are tagged by commit SHA, so a rollback is *Actions → Deploy to Scaleway
→ Run workflow* with `image_tag` set to an earlier SHA.

One-time provisioning — the database, bucket, registry, container, jobs, domain
and GitHub secrets — is in
**[docs/scaleway-setup.md](docs/scaleway-setup.md)**. That document also covers
the two settings that decide the bill (the notifier cron interval and
`min-scale`), backups, and the platform limits worth knowing before you hit
them.

## Project layout

```
config/
  settings/
    base.py           # shared settings (env-driven); SQLite unless DATABASE_URL is set
    dev.py            # DEBUG, console email, stub WhatsApp, SQLite pragmas
    prod.py           # Scaleway: S3 media, connection pooling, SMTP, security headers
    test.py           # pytest settings (SQLite or Postgres via DATABASE_URL)
  health.py           # /_health middleware, ahead of ALLOWED_HOSTS and the HTTPS redirect
  urls.py             # /admin/, /accounts/, /me/, /provider/, /admin-tools/, catalogue at /
deploy/
  gunicorn.conf.py    # tuned for a Serverless Container (1 process, threads, preload)
docs/
  scaleway-setup.md   # one-time provisioning: database, bucket, container, jobs, domain
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
                      # management/commands/run_notifier.py (--once/--drain/daemon)
  dashboards/         # parent, provider and admin-tools views/urls
templates/            # server-rendered HTML (HTMX-enhanced)
static/               # main.css, vendored htmx.min.js
tests/                # pytest suite (services, capacity race, notifications, views,
                      # health probe, notifier job modes)
.github/workflows/
  ci.yml              # tests on SQLite + Postgres, deploy checks, image build
  deploy.yml          # build → migrate job → container redeploy → smoke test
```
