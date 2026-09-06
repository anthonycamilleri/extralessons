# Deploying Extralessons on Scaleway Serverless

End-to-end setup, from an empty Scaleway project to a deploying-on-push
pipeline. Everything is in `fr-par`, the only region that currently offers all
three of Serverless Containers, Serverless Jobs and Serverless SQL Database.

Budget about an hour for the first run. If the estate already exists — it does
for `esljparents.eu` — skip to *Operating it*; the environment is kept in step
by the *Scaleway: configure the estate* workflow, and
[migration-render-to-scaleway.md](migration-render-to-scaleway.md) is the
runbook that brought the data back from Render.

## The short version

Everything below is scripted. `deploy/provision.sh` creates the whole estate,
is idempotent, and can be re-run after a failure — it skips whatever already
exists:

```sh
scw init                                      # once, interactive
cp deploy/scaleway.env.example deploy/scaleway.env
$EDITOR deploy/scaleway.env                   # domain, admin email, ZeptoMail token
./deploy/provision.sh
./deploy/github-config.sh                     # writes the Actions secrets (needs gh)
```

It prints the one thing it cannot do for you — the DNS record — and the exact
command to attach the domain afterwards. The first administrator is created by
the migrate job (`manage.py ensure_admin`) and emailed a set-password link.

The variables every role runs with live in one file,
`deploy/scaleway-env.lib.sh`; `provision.sh` applies them to a fresh estate and
the *Scaleway: configure the estate* workflow to an existing one. Add a setting
there and both paths carry it.

Read the rest of this document to understand what it made and why, or to do it
by hand. The commands here and in the script are the same commands.

## The shape of it

```
                    ┌──────────────────────┐
   parents,         │   Edge Services      │  caching + custom domain + TLS
   providers,  ───► │   (CDN)              │
   office           └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │ Serverless Container │  Django under gunicorn.
                    │ "extralessons-web"   │  Scales 0..N, static files
                    └──────────┬───────────┘  served from inside by WhiteNoise.
                               │
              ┌────────────────┴──────────────────┐
              │                                   │
   ┌──────────▼─────────┐                    ┌────▼─────────────────┐
   │ Serverless SQL DB  │  rows + uploaded   │ ZeptoMail API /      │
   │ (PostgreSQL 16)    │  class images      │ WhatsApp (outbound)  │
   └──────────▲─────────┘  (apps/media)      └────▲─────────────────┘
              │                                   │
   ┌──────────┴──────────────┐        ┌────────────┴─────────────┐
   │ Job: extralessons-      │        │ Job: extralessons-       │
   │      migrate            │        │      notifier            │
   │ run once per deploy     │        │ cron: drains the outbox  │
   └─────────────────────────┘        └──────────────────────────┘
```

One image, three roles. The container serves HTTP; the two jobs run the same
image with a different start command. Nothing is deployed twice, and there is
no bucket: uploaded class images are small JPEGs stored in the database
(`apps/media`), served at `/media/` with immutable caching, and backed up with
everything else.

**Why the notifier is a Job and not a second container.** The outbox worker is a
loop that mostly sleeps. As a container it could never scale to zero, so you
would pay for an idle instance around the clock to send a few dozen emails a
day. As a scheduled Job it wakes, drains the queue, and exits.

## Before you start

Install and authenticate the CLI:

```sh
curl -sS https://raw.githubusercontent.com/scaleway/scaleway-cli/master/scripts/get.sh | sudo sh
scw init
```

Then set the variables the rest of this guide uses:

```sh
export SCW_DEFAULT_REGION=fr-par
export PROJECT_ID=$(scw config get default-project-id)
export APP_NAME=extralessons
export DOMAIN=www.esljparents.eu          # your domain
```

## 1. An IAM application for the app itself

The app authenticates to the database *as an IAM principal* — Serverless SQL
Database has no separate database users. Give the running app its own
application so its permissions are separate from yours and its key can be
rotated without locking you out.

```sh
APP_ID=$(scw iam application create name="$APP_NAME-runtime" \
  description="Extralessons running on Serverless" -o json | jq -r '.id')

# ServerlessSQLDatabaseReadWrite: query the database. Nothing else — the app
# has no bucket to reach.
scw iam policy create name="$APP_NAME-runtime" application-id="$APP_ID" \
  rules.0.project-ids.0="$PROJECT_ID" \
  rules.0.permission-set-names.0=ServerlessSQLDatabaseReadWrite

# The secret key is shown once. Keep it.
scw iam api-key create application-id="$APP_ID" \
  default-project-id="$PROJECT_ID" \
  description="$APP_NAME runtime" -o json
```

The **application ID** is the database username; the API key's **secret key** is
the database password. Export both — the rest of this guide uses them:

```sh
export RUNTIME_ACCESS_KEY=SCWXXXXXXXXXXXXXXXXX
export RUNTIME_SECRET_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Make a second application the same way for CI (`$APP_NAME-ci`) with
`ContainerRegistryFullAccess`, `ContainersFullAccess` and
`ServerlessJobsFullAccess`; export its secret key as `CI_SECRET_KEY`. That key
goes to GitHub, and it deliberately has no database or storage access — CI
pushes images and rolls deployments, nothing more.

## 2. Serverless SQL Database

```sh
scw sdb-sql database create name="$APP_NAME" cpu-min=0 cpu-max=4 -o json
```

`cpu-min=0` lets the database scale to zero: after five minutes without a
query it stops billing compute, and the first query afterwards pays a cold
start of a few seconds.

**Choosing `cpu-min`.** `0` is right here. It only pays off if the database is
allowed to fall idle, which is why the notifier runs nightly rather than every
few minutes — see step 6.

Get the hostname. The console shows it under *Connect application* on the
database's Overview tab; from the CLI:

```sh
scw sdb-sql database list name="$APP_NAME" -o json | jq
```

It looks like `<id>.pg.sdb.fr-par.scw.cloud`. Export it as `DB_HOST`.

Then assemble the connection string. The username is the **application ID** from
step 1, the password is its **secret key**:

```sh
export DATABASE_URL="postgres://${APP_ID}:${RUNTIME_SECRET_KEY}@${DB_HOST}:5432/${APP_NAME}?sslmode=require"
```

`sslmode=require` is mandatory. `config/settings/prod.py` defaults it on if you
forget, but be explicit.

> **Do not point the test suite at this database.** Creating a test database
> needs `CREATE DATABASE`, and Scaleway blocks DDL on databases and users. CI
> runs against a plain `postgres:16` service container; keep it that way.

## 3. Uploaded images: nothing to create

Class cover images (`ActivityClass.image`) cannot live on the container
filesystem: it is ephemeral and per-instance, so an image uploaded through one
instance would 404 on the next request. They live in the database instead
(`apps/media`, selected whenever `S3_BUCKET` is unset): every upload is shrunk
to a JPEG of at most 1600px first, so a class image is one or two hundred
kilobytes and a school's whole catalogue is tens of megabytes. Backups include
them; a database move carries them. `manage.py prune_stored_files` reclaims
the rows replaced images leave behind.

The S3 path is still in the code. If the catalogue ever outgrows the database,
create a bucket, give the runtime application `ObjectStorageFullAccess`, and
set `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID` and
`S3_SECRET_ACCESS_KEY` on the container.

## 4. Container Registry

```sh
scw registry namespace create name="$APP_NAME" is-public=false -o json
```

The registry endpoint is `rg.fr-par.scw.cloud/$APP_NAME`. Log Docker in with
the literal username `nologin` and your secret key as the password:

```sh
docker login rg.fr-par.scw.cloud -u nologin -p "$CI_SECRET_KEY"
```

## 5. Build and push the first image

The container has to exist before you can create it, so push one image by hand.
After this, GitHub Actions does it.

```sh
IMAGE="rg.fr-par.scw.cloud/$APP_NAME/$APP_NAME:bootstrap"

# --platform is not optional: Scaleway Serverless rejects arm64 images, which
# is what you get by default on an Apple Silicon machine.
docker buildx build --platform linux/amd64 --target runtime -t "$IMAGE" --push .
```

## 6. Create the container and the two jobs

First, the environment every one of them shares. `deploy/scaleway-env.lib.sh`
is the authority; the commands below spell it out.

```sh
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
export MCP_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')
export SITE_URL="https://$DOMAIN"
export ZEPTOMAIL_SEND_MAIL_TOKEN=...       # the Mail Agent's Send Mail token
export ADMIN_EMAIL=you@example.com
```

Email goes out through Zoho ZeptoMail's API
(`apps/notifications/backends/zeptomail.py`): verify the sending domain in
ZeptoMail (`zeptomail.zoho.eu` for an EU account), create a Mail Agent, copy its
*Send Mail token*. The token selects the backend; without it the app falls back
to plain SMTP over the `EMAIL_*` variables (port 587 only, see *Things that
will bite you*).

### The web container

```sh
NS_ID=$(scw container namespace create name="$APP_NAME" -o json | jq -r '.id')

scw container container create namespace-id="$NS_ID" name="$APP_NAME-web" \
  image="$IMAGE" \
  port=8080 \
  min-scale=0 max-scale=5 \
  memory-limit-bytes=1GB mvcpu-limit=1000 \
  scaling-option.concurrent-requests-threshold=8 \
  timeout=60s \
  privacy=public \
  https-connections-only=true \
  liveness-probe.http.path=/_health \
  liveness-probe.interval=30s \
  environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod \
  environment-variables.ALLOWED_HOSTS="$DOMAIN" \
  environment-variables.CSRF_TRUSTED_ORIGINS="https://$DOMAIN" \
  environment-variables.SITE_URL="$SITE_URL" \
  environment-variables.TIME_ZONE=Europe/Malta \
  environment-variables.LOG_LEVEL=INFO \
  environment-variables.DEFAULT_FROM_EMAIL="ESLJ Parents <info@$DOMAIN>" \
  environment-variables.ZEPTOMAIL_API_URL=https://api.zeptomail.eu/v1.1/email \
  environment-variables.ADMIN_EMAIL="$ADMIN_EMAIL" \
  environment-variables.DB_POOL_MAX_SIZE=8 \
  environment-variables.NOTIFIER_DRAIN_MAX_SECONDS=300 \
  environment-variables.WHATSAPP_ENABLED=false \
  secret-environment-variables.SECRET_KEY="$SECRET_KEY" \
  secret-environment-variables.DATABASE_URL="$DATABASE_URL" \
  secret-environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="$ZEPTOMAIL_SEND_MAIL_TOKEN" \
  secret-environment-variables.MCP_API_TOKEN="$MCP_API_TOKEN" \
  -o json
```

`ALLOWED_HOSTS` must also carry the endpoint Scaleway generates for the
container (read it back once the container is `ready`; `provision.sh` does
this in a second pass), and a bare apex domain if you accept one alongside
`www`. `container update` replaces the plain map wholesale and merges the
secret map, so every later update re-sends the full plain set.

Why these numbers:

> **These argument names track scaleway-cli v2.61.** An earlier CLI spelled
> them `registry-image`, `memory-limit` (MiB), `cpu-limit`, `http-option` and
> `health-check.*`. Those are not deprecated aliases — they were removed, and
> the CLI now rejects them as unknown arguments. If you are following an older
> copy of this guide, that is why. `scw container container create -h` is the
> authority.

| Setting | Value | Reason |
|---|---|---|
| `scaling-option.concurrent-requests-threshold` | 8 | Matches `GUNICORN_THREADS` in `deploy/gunicorn.conf.py`. Set them together or the platform will queue work on an instance that has no thread free for it. (This replaces the deprecated `max-concurrency` flag.) |
| `memory-limit-bytes` | `1GB` | Must carry a `G`/`GB` unit — the CLI rejects a bare byte count and rejects `MB`. |
| `max-scale` | 5 | 5 × 8 = 40 concurrent requests, far more than a school needs on enrolment day. Raise it *and* `DB_POOL_MAX_SIZE` together — every instance holds up to `DB_POOL_MAX_SIZE` connections, and the database's connection ceiling scales with its allocated compute. |
| `min-scale` | 0 | Scale to zero after 15 idle minutes. Set `1` if a cold start on the first morning request is unacceptable; it costs roughly a small always-on instance. |
| `memory-limit-bytes` | `1GB` | Django plus Pillow. Below 512 MB image uploads get tight. |
| `timeout` | 60s | Must be ≥ `GUNICORN_TIMEOUT` so gunicorn is the one that gives up first and returns a real error. |
| `https-connections-only` | true | Insecure HTTP is turned away at the edge, before a request costs you an instance. This is the replacement for the removed `http-option=redirected`. |
| `liveness-probe.http.path` | `/_health` | `config/health.py` answers before host validation, so a wrong `ALLOWED_HOSTS` cannot fail the probe and take every instance down. |

**One interaction to know about.** Because delivery is inline, a request that
queues notifications also sends them, and it holds its pooled database
connection while it does. For an ordinary transition that is two or three
emails and well under a second. For a broadcast it can be the full
`NOTIFIER_INLINE_MAX_SECONDS` (default 20). With `DB_POOL_MAX_SIZE=4`, four
simultaneous broadcasts would make a fifth request wait — which no single
school will ever do, but it is the reason that budget exists and the knob to
turn if you ever see requests queueing behind sends.

### The migration job

```sh
scw jobs definition create name="$APP_NAME-migrate" \
  image-uri="$IMAGE" \
  cpu-limit=500 memory-limit=1024 \
  job-timeout=600s \
  startup-command.0=python startup-command.1=manage.py \
  args.0=migrate args.1=--noinput \
  environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod \
  environment-variables.SECRET_KEY="$SECRET_KEY" \
  environment-variables.DATABASE_URL="$DATABASE_URL" \
  environment-variables.SITE_URL="$SITE_URL" \
  environment-variables.DEFAULT_FROM_EMAIL="ESLJ Parents <info@$DOMAIN>" \
  environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="$ZEPTOMAIL_SEND_MAIL_TOKEN" \
  environment-variables.ADMIN_EMAIL="$ADMIN_EMAIL" \
  -o json
```

No cron: this one only ever runs from the deploy pipeline, twice per deploy —
once as defined, then once more with `args.0=ensure_admin` (contextual
arguments override the definition's for that run only), which creates the
`ADMIN_EMAIL` superuser with a set-password email the first time and does
nothing thereafter. That is why it carries the email settings.

### The notifier job

Delivery does **not** depend on this job. Notifications go out inline, in the
request that caused them, as soon as the state change commits — see
`schedule_delivery()` in `apps/notifications/services.py`. This job is the
safety net for the three things no user action can trigger.

```sh
scw jobs definition create name="$APP_NAME-notifier" \
  image-uri="$IMAGE" \
  cpu-limit=500 memory-limit=1024 \
  job-timeout=600s \
  cron-schedule.schedule="0 3 * * *" \
  cron-schedule.timezone="Europe/Malta" \
  startup-command.0=python startup-command.1=manage.py \
  args.0=run_notifier args.1=--drain args.2=--max-seconds args.3=240 \
  environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod \
  environment-variables.SITE_URL="$SITE_URL" \
  environment-variables.DEFAULT_FROM_EMAIL="ESLJ Parents <info@$DOMAIN>" \
  environment-variables.SECRET_KEY="$SECRET_KEY" \
  environment-variables.DATABASE_URL="$DATABASE_URL" \
  environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="$ZEPTOMAIL_SEND_MAIL_TOKEN" \
  -o json
```

`--drain` keeps cycling until the outbox is empty, then exits. `--max-seconds
240` keeps a run comfortably inside the 600-second job timeout.

**Why once a night is enough.** The job does three things that inline delivery
cannot, because all three happen when nothing else is happening:

1. **Retries whose backoff came due while the site was idle.** A failed send is
   rescheduled 2, 4, 8… minutes out. No click will land at that moment.
2. **Rows stranded in `SENDING`** because an instance died mid-send. By
   definition whatever would have retried them is gone.
3. **Waiting-list offers reaching their deadline**, 48 hours after an admin
   made them.

None of these needs to be prompt, for a reason worth understanding before you
change the schedule: **a delivery pass claims every due row, not just the ones
the current request queued.** So any parent registering or any admin approving
also flushes everyone else's backlog — including retries. During school hours
the site's own traffic is the notifier. The cron only matters when the site is
genuinely idle, and when it is idle nobody is waiting for anything.

Offer expiry is safe to defer for a second reason: it is already lazy.
`_seats_taken()` and the `with_counts()` annotation both exclude expired offers
in SQL, and `_expire_stale_offers_locked()` runs on every transition touching
that class. A freed seat appears in the catalogue and the review queue the
instant it frees, whether or not this job has run. What the sweep adds is the
*notification* that an offer lapsed.

**What you give up at `0 3 * * *`:** on a completely idle site, an
offer-expiry email and a crash-stranded notification can wait until 03:00.
If that ever bites, make it hourly (`0 * * * *`) or every fifteen minutes
during school hours (`*/15 7-19 * * *`) — during those hours real traffic keeps
the database awake anyway, so it costs close to nothing. The saving from
scaling to zero comes from nights, weekends and holidays, and a nightly job
keeps all of it.

> **Secrets in job definitions.** The commands above put `DATABASE_URL`,
> `SECRET_KEY` and the email token in plain environment variables, readable by
> anyone who can read the job definition — including the CI key, which is what
> lets the migration workflow find the database without a human. Once things
> have settled, move them to Secret Manager and reference them instead:
> ```sh
> SECRET_ID=$(scw secret secret create name=extralessons-database-url -o json | jq -r .id)
> scw secret version create "$SECRET_ID" data="$DATABASE_URL"
> scw jobs secret create job-definition-id="$JOB_ID" \
>   secrets.0.secret-manager-id="$SECRET_ID" \
>   secrets.0.secret-manager-version=latest \
>   secrets.0.env-var-name=DATABASE_URL
> ```
> then remove the plain variable from the definition. The runtime application
> needs `SecretManagerSecretAccess` (not `ReadOnly`, which is metadata only)
> to read the version at run time. Containers have this built in already —
> that is what `secret-environment-variables` above is.

## 7. Run the first migration and create an admin

```sh
MIGRATE_ID=$(scw jobs definition list -o json | jq -r '.[] | select(.name=="extralessons-migrate") | .id')
deploy/scw-run-job.sh "$MIGRATE_ID"                      # migrate
deploy/scw-run-job.sh "$MIGRATE_ID" args.0=ensure_admin  # first admin, emailed a set-password link
```

`scw jobs definition start` takes contextual `args` that apply to that run
only, leaving the definition alone; `deploy/scw-run-job.sh` wraps start, wait
and the success check. The same trick runs any management command against
production — `prune_stored_files`, a data fix, `ensure_admin --send-reset`
to resend the link.

## 8. Custom domain, TLS and CDN

```sh
CONTAINER_ID=$(scw container container list name="$APP_NAME-web" -o json | jq -r '.[0].id')
scw container container get "$CONTAINER_ID" -o json | jq -r '.domain_name'
```

Point a `CNAME` for `$DOMAIN` at that hostname, then:

```sh
scw container domain create container-id="$CONTAINER_ID" hostname="$DOMAIN"
```

or run *Scaleway: attach domains* from Actions. Scaleway checks the record at
creation (so DNS has to move first), then issues and renews a Let's Encrypt
certificate itself. For a bare apex (`esljparents.eu`) alongside `www`: an
apex cannot be a CNAME, so use an ALIAS/ANAME record at the registrar (or
Scaleway Domains and DNS, which has them), add the apex to `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` (the `APEX_DOMAIN` setting does this), and attach it
the same way.

Optionally add an Edge Services pipeline in front for caching. Static files
already carry year-long immutable cache headers (hashed filenames via
WhiteNoise's manifest storage), and so do the images served at `/media/`, so
a CDN absorbs almost all asset traffic without ever waking an instance. Not
needed for a school's traffic; a cost knob for later.

## 9. Wire up GitHub Actions

Under *Settings → Secrets and variables → Actions*:

**Secrets** (from the CI application in step 1):

| Name | Value |
|---|---|
| `SCW_ACCESS_KEY` | CI API key access key |
| `SCW_SECRET_KEY` | CI API key secret key |
| `SCW_DEFAULT_PROJECT_ID` | `scw config get default-project-id` |
| `SCW_DEFAULT_ORGANIZATION_ID` | `scw config get default-organization-id` |

**Variables:**

| Name | How to find it |
|---|---|
| `SCW_REGISTRY_NAMESPACE` | the namespace name, e.g. `extralessons` |
| `SCW_CONTAINER_ID` | `scw container container list -o json \| jq -r '.[0].id'` |
| `SCW_JOB_MIGRATE_ID` | `scw jobs definition list name=extralessons-migrate -o json \| jq -r '.[0].id'` |
| `SCW_JOB_NOTIFIER_ID` | `scw jobs definition list name=extralessons-notifier -o json \| jq -r '.[0].id'` |
| `APP_URL` | `https://activities.example.com` (optional; enables the post-deploy smoke test) |

Create the `production` environment under *Settings → Environments* (the
deploy workflow is pinned to it; required reviewers there give you a manual
approval step if you ever want one). Then push to `main`: once *CI* is green,
`.github/workflows/deploy.yml` takes it from there — build for `linux/amd64`,
push, run migrations as a job and wait for them, repoint the notifier job,
redeploy the container, then smoke-test `/_health`.

Four more workflows use the same secrets from *Actions → Run workflow*:
*Inspect hosting estate* (read-only listing), *Scaleway: configure the estate*
(apply `deploy/scaleway-env.lib.sh` to the container and jobs), *Scaleway:
attach domains*, and *Scaleway: move production from Render* (the data copy).

Note what is *not* in GitHub: no database URL, no SMTP password, no WhatsApp
token. Application secrets live in Scaleway; GitHub only gets a key that can
push images and roll deployments.

## Operating it

**Logs.** Everything goes to stdout and is collected by Cockpit
(`scw cockpit`). The health probe is filtered out of the access log on purpose
— it runs constantly, says nothing, and log ingestion is billed by volume.

**Rolling back.** Images are tagged by commit SHA, so:

```
Actions → Deploy to Scaleway → Run workflow → image_tag: <previous sha>
```

This skips the build and repoints everything at the old image. It does **not**
un-apply migrations — if the bad deploy included a destructive migration you
are restoring from a backup instead, which is the usual reason to keep
migrations additive and ship them a deploy ahead of the code that needs them.

**Backups.** Serverless SQL Database is backed up automatically. For an
off-Scaleway copy, `SOURCE_DATABASE_URL=$DATABASE_URL deploy/migrate-db.sh
--dump-only` from a laptop writes a `pg_dump` custom-format file (and the row
counts to check a restore against) to `deploy/dumps/`, gitignored. Uploaded
images are in it. Restoring one elsewhere is the same script with
`--restore-only`. Note the dump holds families' personal data.

**Checking a deployment.** `deploy/smoke.sh https://www.esljparents.eu`
exercises the probe, the public pages, login and admin, hashed statics, the
security headers and `/mcp` from outside; add `MCP_API_TOKEN=...` to make a
real tool call.

**Freezing the site.** `MAINTENANCE_MODE=true` on the container answers
everything but the health probe with a 503 and `Retry-After`
(`config/maintenance.py`) — for copying the database somewhere while nothing
can write to it.

**Cost knobs, roughly in order of impact:**

1. `min-scale` on the container.
2. `cpu-min` on the database.
3. The notifier cron interval — a nightly job lets the database idle through
   every night, weekend and holiday. Anything more frequent than about every
   five minutes keeps it permanently awake, because idle only starts after five
   minutes of silence.
4. Log volume in Cockpit.

## Things that will bite you

**Outbound SMTP on ports 25 and 465 is blocked**, except to Scaleway's own mail
servers. Port 587 works, which is what `.env.example` uses. If you bring an
external provider that only offers implicit TLS on 465, it will fail silently
from the platform's point of view — the notification rows will just accumulate
retries. Scaleway Transactional Email on 587 avoids the question.

**ARM images are rejected.** Building on an Apple Silicon Mac without
`--platform linux/amd64` produces an image that fails at deploy time, not at
build time.

**On Windows, Git Bash rewrites `/_health`.** MSYS translates any argument that
looks like a Unix absolute path into a Windows one, so
`liveness-probe.http.path=/_health` arrives at the API as
`C:/Program Files/Git/_health`. The container then fails every health check and
settles in `error` with a message naming that path — which at least says what
happened, once you know to read it. `export MSYS_NO_PATHCONV=1` before running
any `scw` command fixes it; `deploy/provision.sh` sets it for you. The same
applies to `docker run -v /app:...` and anything else taking a leading slash.
PowerShell and WSL are unaffected.

**A partial liveness probe is rejected at the API, not the CLI.** Passing only
`liveness-probe.http.path` and `.interval` parses fine and then fails with
`'liveness_probe.timeout' is required`. Supply all four:
`.http.path`, `.interval`, `.timeout` and `.failure-threshold` (which must be
between 3 and 50).

**The container endpoint is assigned asynchronously.** `domain_name` is `null`
for the first minute or two after creation, so read it in a loop rather than
once — and note that a container stuck in `creating` is often really a failing
health check that has not yet been reported as `error`.

**`ALLOWED_HOSTS` must include every hostname that reaches Django**, including
the generated `…functions.fnc.fr-par.scw.cloud` endpoint if you use it directly.
The health probe is exempt: `config/health.py` answers it before host validation
precisely so a misconfigured `ALLOWED_HOSTS` cannot take the instances down.

**Advisory locks are not guaranteed** on Serverless SQL Database. This app does
not use them — the capacity mutex in `apps/enrollments/services.py` is a
row-level `SELECT … FOR UPDATE` inside a transaction, which the pooler honours
because it pins the connection for the transaction's duration. If you ever
reach for `pg_advisory_lock`, it will appear to work and then quietly not.

**The database scales to zero, and pooled connections do not know.** With
`cpu-min=0` the database stops after five minutes without queries and the
first query afterwards pays a cold start of about three seconds — fine. What
is not fine is a connection the app's pool kept open across the stop: the
backend behind it is gone, and its first use fails with `OperationalError:
internal error`, one 500 for whoever arrives first. `config/settings/prod.py`
therefore turns on `CONN_HEALTH_CHECKS`, so the pool checks each connection
as it hands it out, and closes connections idle for
longer than `DB_POOL_MAX_IDLE` (120 s, under the database's five minutes), so
the dead one is replaced rather than served. If cold starts themselves become
a complaint, `scw sdb-sql database update <id> cpu-min=1` keeps the database
warm around the clock, at the cost of one vCPU billed continuously.

**Session settings leak between pooled clients.** Serverless SQL Database sits
behind a connection pooler that does not reset session state when one client
disconnects and another is given the same backend — `SET`, `RESET`,
`search_path` are all documented as shared. The one place this bit: `pg_dump`
output starts by setting `search_path` to nothing for its session, so after a
`pg_restore` the next clients on that backend could not see any table by its
bare name (the app answered 500, `psql` said the tables did not exist).
`deploy/migrate-db.sh` runs `RESET ALL` on a few fresh connections after
restoring and schema-qualifies its own queries. Anything else you run against
the database by hand that changes session settings should wrap them in a
transaction (`SET LOCAL`) or reset them afterwards.

**Redirect loops.** If `/` bounces forever, the platform is forwarding plain
HTTP without `X-Forwarded-Proto`. Set `SECURE_SSL_REDIRECT=false` on the
container and let `https-connections-only=true` keep plain HTTP out at the
edge.
