# Extralessons

A booking system for school extra-curricular activities. The school publishes a public catalogue of classes for the term; parents create an account, add their children, and request places. The school office reviews every request, manages waiting lists, and hand-picks who gets a freed seat. Providers (coaches, tutors) run their classes from their own dashboard. Families are kept in the loop by email and WhatsApp.

## Features

**Public**
- Browse the catalogue of published classes for the active term (recommended age range, schedule, provider, seats, first or next class date) without an account.

**Parents**
- Self-service signup (can be switched off by the school), add children with their class (P1–P5, S1–S7, English or Slovenian section), date of birth, whether they may go home on their own, and notes for providers.
- Read the programme's terms and conditions (Markdown, edited in the admin, linked from every page) and confirm them on each registration; the confirmation is stamped on the enrolment.
- Change their password while logged in without retyping the current one.
- Multi-guardian support: invite a co-parent by email to share access to a child.
- Request a place in a class; duplicates are blocked automatically, and a child outside the class's recommended age range gets a "this is recommended for ages X–Y — continue?" step rather than a refusal (the mismatch is flagged to the office on the review queue).
- Confirm or decline waiting-list offers before they expire; withdraw from a class at any time.
- Per-user notification preferences (email and/or WhatsApp).

**Providers**
- Dashboard with class rosters for their own classes.
- Per-session attendance taking.
- Message the families of their classes (announcements/broadcasts).

**School admin**
- Set up the school year once — term dates plus every holiday period — and every class generated in it skips those days automatically.
- Review queue: approve or reject enrollment requests (approve enrolls directly if a seat is free, otherwise waitlists).
- When a seat frees up, hand-pick which waitlisted family gets the offer; offers expire automatically after a configurable number of hours (default 48).
- Optional email alerts on new requests and freed seats.
- Manage terms, classes, providers, notification templates, and site-wide settings in the Django admin; admin tools dashboard at `/admin-tools/`.
- Let Claude do the data entry: a built-in MCP server (`manage.py mcp_server`) lets Claude Code or Claude Desktop set up school years, holidays, terms, providers and classes from a conversation — see [Connecting Claude](#connecting-claude-mcp).

## Architecture at a glance

Django 5 + PostgreSQL, server-rendered templates progressively enhanced with
HTMX (vendored, no JS build step). Static files via WhiteNoise, uploaded class
images in the database (`apps/media`, served at `/media/` with immutable
caching; an S3 bucket is a one-variable switch), email through Zoho
ZeptoMail's API.

The whole thing runs on Render, as one image in three roles, all declared in
[`render.yaml`](render.yaml):

| Role | Runs as | Command |
|------|---------|---------|
| `web` | Web service (Docker) | `gunicorn --config deploy/gunicorn.conf.py config.wsgi` |
| `migrate` | The web service's pre-deploy command, once per deploy | `manage.py migrate --noinput` |
| `notifier` | Cron job, nightly | `manage.py run_notifier --drain` |

Three roles rather than three images: a migration that ran against code the web
tier does not have is exactly the failure that arrangement avoids. Locally the
same code runs from `docker-compose.yml`, or with no services at all against
SQLite.

Full deployment instructions: [docs/render-setup.md](docs/render-setup.md).
(The previous Scaleway Serverless setup is kept, un-triggered, under
[docs/scaleway-setup.md](docs/scaleway-setup.md).)

**Transactional outbox.** State changes never talk to SMTP or the WhatsApp API
directly. Instead, `Notification` rows are queued inside the same database
transaction as the state change (`apps/notifications/services.py`), so they
commit atomically with it. The notifier (`apps/notifications/worker.py`) claims
batches with `SELECT ... FOR UPDATE SKIP LOCKED`, sends outside any transaction,
and retries failures with exponential backoff up to `NOTIFIER_MAX_ATTEMPTS`. It
also expires overdue waiting-list offers each cycle. Every row is a permanent
delivery log, inspectable in the admin.

**Delivery happens inline.** Once the state change commits, the same request
delivers what it queued (`schedule_delivery()` in
`apps/notifications/services.py`), sharing one SMTP connection across the
batch. A parent gets their email in seconds, and no polling loop is involved.
This is only safe because the outbox already treats a failed send as a
first-class state: an inline failure leaves exactly the row a failed worker
send would have left, so it costs a retry rather than a lost notification —
and a rolled back transaction announces nothing.

**The scheduled job is a safety net, not the delivery path.** It runs once a
night and handles the three things no click can trigger: retries whose backoff
came due while the site was idle, rows stranded in `SENDING` by a crash, and
waiting-list offers reaching their 48-hour deadline. It can be that rare
because a delivery pass claims *every* due row, not just the current request's
— so during school hours the site's own traffic flushes the queue — and
because expired offers already stop holding a seat in SQL
(`ActivityClassQuerySet.with_counts`), so seat availability never waits for a
sweep.

There is no queue broker and no always-on worker: the outbox lives in the
database the app already has.

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

**The school calendar is a default, not a cage.** Holidays live once, on the
`SchoolYear`, and every term in that year inherits them
(`apps/catalog/models.py`). `generate_sessions()` reconciles a class's
`ClassSession` rows against three inputs — the term dates, the class weekday,
and the year's holidays — and is idempotent, so it is safe to re-run at any
time (admin action *"Regenerate sessions"*). Two escape hatches sit above the
default, because a holiday club is a real thing and so is a one-off make-up
session:

| Override | Where | Effect |
|---|---|---|
| `ActivityClass.runs_during_holidays` | the class | the whole class ignores holidays |
| `ClassSession.holiday_override` | one session | that date survives reconciliation |

Editing a holiday takes the already-generated sessions with it — a `post_save`
/ `post_delete` signal re-reconciles every class in the year, so the holiday
list is not just a rule for classes published afterwards. Two things are never
removed: sessions that already have attendance, and sessions in the past.
Adding a holiday retroactively closes future dates, not history.

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
at http://localhost:8000/admin/. Notifications deliver inline, so they print to
the console (dev settings use the console email backend) as you click. To
exercise the nightly safety net — offer expiry, stuck-row recovery — run
`python manage.py run_notifier --once`.

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

## Connecting Claude (MCP)

The fastest way to populate a term is to hand the paperwork to Claude. The app
ships a [Model Context Protocol](https://modelcontextprotocol.io) server that
Claude Code and Claude Desktop can launch locally. Paste in the PTA's club
list, a provider's PDF or last year's timetable and ask Claude to set it up;
it calls the tools below and the results appear in the Django admin.

What Claude can do through it:

- Read: the school overview, every class with seat counts, a class's session dates.
- Write: school years and their holidays, terms, providers, classes; publish
  (which generates the session calendar), regenerate sessions, archive, cancel.

What it deliberately cannot do: see or change parents, children or individual
enrolments, approve requests, or send messages. Enrolment figures are
aggregates only. Every write uses the same validation and service functions
as the admin, so a class Claude creates skips the school holidays like any
other.

### Hosted: connect to the live site (recommended)

The web service serves the same tools over HTTPS at `/mcp`
(`apps/catalog/mcp_http.py`), so Claude Desktop, Cowork and claude.ai connect
to the hosted app as a **custom connector**. Nothing to install, and the
database stays private.

1. In the Render dashboard, open the web service → **Environment** and copy
   the value of `MCP_API_TOKEN` (Render generated it from `render.yaml`).
2. In Claude: **Settings → Connectors → Add custom connector**. Name it
   `Extralessons`, URL `https://extralessons-web.onrender.com/mcp` (or the
   custom domain once attached). Set **Authentication** to **None** (the
   dialog may say it detected OAuth; it did not, it saw a 401). Under
   **Additional request headers** add one header: `X-API-Key` with the token
   as its value (`Authorization: Bearer <token>` also works if the dialog
   allows that name). Add.
3. Enable the connector in a chat or a Cowork task and ask for the school
   overview.

The token grants everything the tools can do, which includes publishing
classes to parents; treat it like an office login. Rotate it by clearing the
value in Render and re-syncing the Blueprint, then updating the connector.

### Local: run the server on your machine

The stdio server is the same code and is right for development against a
local database, or for Claude Code in this repository.

```sh
pip install -e ".[dev]"
python manage.py migrate           # the server talks to whatever DATABASE_URL points at
python scripts/mcp_smoke.py        # optional: starts the server and calls get_overview
```

**Claude Code.** The repo contains a project-scoped `.mcp.json`, so open the
project directory in Claude Code (with the virtualenv activated so `python` is
the right one) and approve the `extralessons` server when prompted. To register
it by hand instead:

```sh
claude mcp add extralessons -- /path/to/extralessons/.venv/bin/python /path/to/extralessons/manage.py mcp_server
```

**Claude Desktop.** Add the server to `claude_desktop_config.json`
(Settings → Developer → Edit Config). Use absolute paths; Desktop does not
know about your shell or virtualenv:

```json
{
  "mcpServers": {
    "extralessons": {
      "command": "/path/to/extralessons/.venv/bin/python",
      "args": ["/path/to/extralessons/manage.py", "mcp_server"],
      "env": {
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "DATABASE_URL": "sqlite:////path/to/extralessons/db.sqlite3"
      }
    }
  }
}
```

Restart Claude Desktop and the tools appear under the hammer icon.

**Pointing the local server at production** is possible (set `DATABASE_URL`
in the `env` block to the database's *external* URL and add your IP to its
allow list) but the hosted connector above does the same job without exposing
the database. Either way, treat it exactly like `manage.py shell` against
production: writes take effect immediately and parents see published classes
at once. Prefer creating classes as drafts and publishing them from the admin
once you have looked them over.

### What to ask it

- "Create the 2026/27 school year, 1 September to 30 June, with these holidays: …"
- "Add an Autumn term from 7 September to 18 December and make it the active one."
- "Here is AllStars' club list for autumn. Add each class under the AllStars provider, ages and times as listed, capacity 16, as drafts."
- "Which published classes still have free places, and how many sessions does each have?"
- "Move Chess Club to Thursdays at 15:45 and regenerate its sessions."

### Tools

| Tool | Does |
| --- | --- |
| `get_overview` | School name and settings, school years with holidays, terms, providers, class counts by status. Claude calls this first. |
| `list_classes(term?, status?)` | Classes with `enrolled_count`, `waitlist_count`, `requested_count`, `places_free`, `session_count` and their numeric ids. |
| `get_class(class_id)` | One class in full: description, practical details, session dates, skipped holidays. |
| `upsert_school_year(name, start_date, end_date)` | Create or update by name. |
| `upsert_holiday(school_year, name, start_date, end_date)` | Inclusive dates; existing calendars are re-reconciled immediately. |
| `upsert_term(name, start_date, end_date, school_year?, is_active?)` | Create or update; `is_active` left out keeps the current flag. |
| `upsert_provider(name, description?, contact_email?, contact_phone?)` | Create or update by name. Linking user accounts stays in the admin. |
| `upsert_class(term, provider, title, description, age_min, age_max, weekday, start_time, end_time, capacity?, location?, extra_details?, runs_during_holidays?, slug?, rebuild_sessions?)` | Create as DRAFT or update; matched by slug within the term. Updating never touches existing lesson dates; the result flags a schedule change, and `rebuild_sessions` opts in to regenerating them. |
| `publish_class(class_id)` | Set PUBLISHED and generate sessions around the school holidays. |
| `regenerate_sessions(class_id)` | Re-run the calendar reconciliation only. Cancelled lessons stay cancelled. |
| `cancel_sessions(class_id, dates, notes?)` | Cancel individual lessons by date. They stay in the calendar marked cancelled, survive regeneration, and are refused once attendance is recorded. |
| `restore_sessions(class_id, dates)` | Undo `cancel_sessions`. |
| `delete_term(name, school_year?)` | Delete a term that has no classes. |
| `archive_class(class_id)` | Archive a finished class; refused while enrolments are active. |
| `cancel_class(class_id)` | Cancel the class and every family's place, notifying them. Claude is told to confirm with you first. |

Dates are `YYYY-MM-DD`, times `HH:MM`, weekdays `0` (Monday) to `6` (Sunday).
Upserts are idempotent, so re-running a conversation updates rather than
duplicates. Errors come back as plain sentences ("No provider named 'Allstars'.
Known providers: AllStars Sports, Bright Minds.") so Claude can correct itself.

Two transports, one tool set. Over stdio, Claude starts the process and talks
to it on stdin/stdout with no token to manage. Over HTTPS, `/mcp` speaks MCP's
stateless Streamable HTTP behind a bearer token, implemented as a plain Django
view so the app stays one WSGI process. The tool code lives in
`apps/catalog/mcp_server.py`; add a function there and to `TOOLS` and both
transports expose it.

## Configuration

### Environment variables

Copy `.env.example` to `.env` for local development. In production these are
set on the Render services instead of in a file, most of them from
`render.yaml` — see [docs/render-setup.md](docs/render-setup.md).

| Variable | Purpose |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` in production, `config.settings.dev` locally |
| `SECRET_KEY` | Django secret key — set to a long random string |
| `DEBUG` | Keep `false` outside development |
| `ALLOWED_HOSTS` | Comma-separated hostnames the app serves. On Render the generated `*.onrender.com` hostname is added automatically |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins, e.g. `https://activities.example.com` (the Render hostname is added automatically) |
| `SITE_URL` | Absolute base URL used in notification links. Defaults to the Render URL until a custom domain is set |
| `TIME_ZONE` | Default `Europe/Malta` |
| `LOG_LEVEL` | Root log level; everything goes to stdout |
| `DATABASE_URL` | Unset = SQLite. On Render, wired from the database by `render.yaml` |
| `DB_SSLMODE` | Default `require`; `prefer` if the database offers no TLS on the private network |
| `DB_POOL` / `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | Client-side connection pool (PostgreSQL only). Raise `DB_POOL_MAX_SIZE` and the instance count together |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` | Optional S3-compatible bucket for uploaded class images. Unset = stored in the database (production) or on local disk (development) |
| `MEDIA_MAX_AGE` | Cache lifetime for served uploads (default one year; names are never reused) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Object storage credentials |
| `S3_CUSTOM_DOMAIN` | Optional CDN hostname for media URLs |
| `ZEPTOMAIL_SEND_MAIL_TOKEN` | Zoho ZeptoMail Mail Agent token. When set, production sends through ZeptoMail's API (`apps/notifications/backends/zeptomail.py`) |
| `ZEPTOMAIL_API_URL` | Default `https://api.zeptomail.eu/v1.1/email` (EU data centre); `.com` accounts use `https://api.zeptomail.com/v1.1/email` |
| `ZEPTOMAIL_BOUNCE_ADDRESS` | Optional bounce address configured on the Mail Agent |
| `EMAIL_BACKEND` | Override the automatic choice (ZeptoMail API when the token is set, SMTP otherwise) |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` | SMTP, when not using the ZeptoMail API. Use 587 with STARTTLS |
| `DEFAULT_FROM_EMAIL` | From address, e.g. `School Activities <notifications@example.com>`. Must be on a ZeptoMail-verified domain |
| `WHATSAPP_ENABLED` | `false` = log WhatsApp messages instead of sending (stub) |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_API_VERSION` | Meta WhatsApp Cloud API credentials |
| `ADMIN_EMAIL` | First administrator. `manage.py ensure_admin` (run by the pre-deploy step) creates the superuser and emails a set-password link |
| `MCP_API_TOKEN` | Bearer token for the `/mcp` custom-connector endpoint. Empty = endpoint off. Generated by Render |
| `NOTIFIER_BATCH_SIZE` | Notifications delivered per worker cycle (default 20) |
| `NOTIFIER_MAX_ATTEMPTS` | Retries before a notification is marked failed (default 5) |
| `NOTIFIER_DRAIN_MAX_SECONDS` | Time budget for `run_notifier --drain` (default 300) |
| `NOTIFIER_INLINE_DELIVERY` | Deliver in the request that queued the rows (default `true`). Off falls back to the scheduled job alone |
| `NOTIFIER_INLINE_MAX_SECONDS` | Latency budget for an inline pass (default 20); leftovers stay queued |

### Runtime configuration (Django admin)

Most day-to-day settings are editable in the admin without redeploying, and both are seeded with sensible defaults:

- **Site configuration** (singleton): school name, the sender name that signs every email (default "European School PTA"), contact email, catalogue intro text, whether parent self-signup is open, waiting-list offer expiry in hours, toggles for the admin alert emails (new request, seat freed), and the terms and conditions in Markdown (served at `/terms/`, linked from the navigation and footer, and required as a tick on the registration form; empty hides all three).
- **School years and holidays**: a `SchoolYear` holds the calendar; its `School holiday` rows (half-terms, Christmas, public holidays — inclusive date ranges) are the system-level default. Terms point at a school year and inherit them. "Copy holidays into another school year…" sets next year up from this one, shifted by whole weeks so periods keep their weekdays.
- **Notification templates** (one row per event): email subject/body as Django template strings (context includes `school_name`, `sender_name`, `contact_email`, `site_url`, `parent_name`, `parent_first_name`, `child_name`, `child_first_name`, `class_title`, `provider_name`, `schedule`, `location`, `term_name`, `action_url`, `offer_expires_at`, ...), an enabled flag, plus the WhatsApp mapping — approved template name, language, and which context keys fill the `{{1}}..{{n}}` placeholders. Leave the WhatsApp template name empty to skip WhatsApp for that event. The defaults are written as a parent volunteer would write to another parent and signed by `sender_name`; the data migration that seeds them only rewrites rows still carrying the previous default wording, so edits made in the admin survive deploys.

## WhatsApp setup

WhatsApp delivery uses the Meta WhatsApp Cloud API and sends business-initiated *template* messages only. You need:

1. A Meta WhatsApp Business account with a registered phone number — note the **phone number ID** and create a permanent **access token**.
2. Message templates created and **pre-approved in Meta Business Manager**, one per notification event you want on WhatsApp.
3. Fill each approved template's name (and language/parameter order) into the matching **Notification template** row in the Django admin.
4. In `.env`, set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and `WHATSAPP_ENABLED=true`.

With `WHATSAPP_ENABLED=false` (the default, and always in dev settings) a stub adapter logs messages instead of calling Meta; the production settings switch to the real `WhatsAppCloudAdapter` only when the flag is true. Parents opt in per account and must have a phone number in international format.

## Deployment

Production runs on Render, described end to end by `render.yaml`. Push to
`main`; once `CI` is green, `.github/workflows/deploy-render.yml` asks Render
to deploy that commit: Render builds the image from the `Dockerfile`, runs
`manage.py migrate` and `manage.py ensure_admin` as the web service's
pre-deploy command, and switches traffic only when the new version answers
`/_health`. The workflow finishes by smoke-testing `/_health` itself. The
notifier cron job is rebuilt by Render on its own once CI is green, because
Render's API cannot deploy cron jobs.

A rollback is *Actions → Deploy to Render → Run workflow* with `commit_sha` set
to an earlier commit (or *Rollback* on the deploy in the Render dashboard).

One-time setup — connecting the GitHub repo, creating the Blueprint, secrets,
domain, object storage, the first admin — is in
**[docs/render-setup.md](docs/render-setup.md)**, along with backups, costs and
how to move the data over from Scaleway. The old Scaleway pipeline
(`deploy.yml`, `deploy/provision.sh`, `docs/scaleway-setup.md`) is kept but
only runs by hand.

## Project layout

```
config/
  settings/
    base.py           # shared settings (env-driven); SQLite unless DATABASE_URL is set
    dev.py            # DEBUG, console email, stub WhatsApp, SQLite pragmas
    prod.py           # production: S3 media, connection pooling, SMTP, security headers
    test.py           # pytest settings (SQLite or Postgres via DATABASE_URL)
  health.py           # /_health middleware, ahead of ALLOWED_HOSTS and the HTTPS redirect
  urls.py             # /admin/, /accounts/, /me/, /provider/, /admin-tools/, catalogue at /
render.yaml           # Render Blueprint: web service, notifier cron job, Postgres, shared env
deploy/
  gunicorn.conf.py    # tuned for a small, horizontally scaled container (1 process, threads, preload)
  render-deploy.sh    # deploy one commit to one Render service via the API and wait for it
  render-github-config.sh  # write the Render API key and service IDs into GitHub Actions
  provision.sh, github-config.sh, scaleway.env.example   # legacy Scaleway provisioning
docs/
  render-setup.md     # one-time setup: GitHub connection, Blueprint, secrets, domain, storage
  scaleway-setup.md   # legacy: the previous Scaleway Serverless estate
apps/
  accounts/           # custom email-login User (roles: ADMIN/PROVIDER/PARENT),
                      # Child, Guardian, GuardianInvite, SiteConfig singleton,
                      # management/commands/seed_demo.py
  catalog/            # SchoolYear + Holiday (the school calendar), Provider, Term,
                      # ActivityClass, ClassSession; generate_sessions() reconciles a
                      # class's dates against the schedule and the year's holidays;
                      # public catalogue views; mcp_server.py = the tools Claude uses,
                      # served by management/commands/mcp_server.py over stdio and by
                      # mcp_http.py at /mcp for Claude Desktop / Cowork custom connectors
  enrollments/        # Enrollment + Attendance models;
                      # services.py = ALL state transitions (register/approve/reject/
                      # offer/confirm/decline/expire/cancel) under a per-class row lock
  notifications/      # NotificationTemplate, Broadcast, Notification (outbox rows);
                      # services.py = queueing/rendering (call inside the state-change
                      # transaction); worker.py = delivery loop (claim, send, retry,
                      # expire offers); channels/ = email + WhatsApp adapters (stub & Meta);
                      # backends/zeptomail.py = Django email backend for ZeptoMail's API;
                      # management/commands/run_notifier.py (--once/--drain/daemon)
  dashboards/         # parent, provider and admin-tools views/urls
  media/              # StoredFile + DatabaseStorage: uploads kept in Postgres, served at
                      # /media/<name> immutably; prune_stored_files removes orphans
templates/            # server-rendered HTML (HTMX-enhanced)
static/               # main.css (the whole design system), vendored htmx.min.js,
                      # img/ (PTA logo + generated favicons), fonts/ (self-hosted
                      # Fredoka woff2 + OFL licence — no third-party font requests)
scripts/              # make_icons.py: regenerate the favicons from the logo;
                      # mcp_smoke.py: start the MCP server over stdio and call a tool
tests/                # pytest suite (services, capacity race, notifications, views,
                      # school holidays, health probe, inline delivery, notifier job modes,
                      # MCP tools)
.mcp.json             # Claude Code picks up the MCP server from here
.github/workflows/
  ci.yml              # tests on SQLite + Postgres, deploy checks, image build
  deploy-render.yml   # after CI: deploy web (migrates first) → deploy notifier → smoke test
  deploy.yml          # legacy Scaleway deploy, manual trigger only
```
