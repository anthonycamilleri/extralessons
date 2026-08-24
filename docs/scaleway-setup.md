# Deploying Extralessons on Scaleway Serverless

End-to-end setup, from an empty Scaleway project to a deploying-on-push
pipeline. Everything is in `fr-par`, the only region that currently offers all
three of Serverless Containers, Serverless Jobs and Serverless SQL Database.

Budget about an hour for the first run.

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
              ┌────────────────┼──────────────────┐
              │                │                  │
   ┌──────────▼─────────┐  ┌───▼────────────┐  ┌──▼──────────────┐
   │ Serverless SQL DB  │  │ Object Storage │  │ SMTP / WhatsApp │
   │ (PostgreSQL)       │  │ (class images) │  │ (outbound only) │
   └──────────▲─────────┘  └────────────────┘  └──▲──────────────┘
              │                                    │
   ┌──────────┴──────────────┐        ┌────────────┴─────────────┐
   │ Job: extralessons-      │        │ Job: extralessons-       │
   │      migrate            │        │      notifier            │
   │ run once per deploy     │        │ cron: drains the outbox  │
   └─────────────────────────┘        └──────────────────────────┘
```

One image, three roles. The container serves HTTP; the two jobs run the same
image with a different start command. Nothing is deployed twice.

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
export DOMAIN=activities.example.com      # your domain
```

## 1. An IAM application for the app itself

The app authenticates to the database *as an IAM principal* — Serverless SQL
Database has no separate database users. Give the running app its own
application so its permissions are separate from yours and its key can be
rotated without locking you out.

```sh
APP_ID=$(scw iam application create name="$APP_NAME-runtime" \
  description="Extralessons running on Serverless" -o json | jq -r '.id')

# ServerlessSQLDatabaseReadWrite: query the database.
# ObjectStorageFullAccess:        read/write the media bucket.
scw iam policy create name="$APP_NAME-runtime" application-id="$APP_ID" \
  rules.0.project-ids.0="$PROJECT_ID" \
  rules.0.permission-set-names.0=ServerlessSQLDatabaseReadWrite \
  rules.1.project-ids.0="$PROJECT_ID" \
  rules.1.permission-set-names.0=ObjectStorageFullAccess

# The secret key is shown once. Keep it.
scw iam api-key create application-id="$APP_ID" \
  description="$APP_NAME runtime" -o json
```

The **application ID** is the database username; the API key's **secret key** is
the database password *and* the Object Storage secret key. Export both — the
rest of this guide uses them:

```sh
export RUNTIME_ACCESS_KEY=SCWXXXXXXXXXXXXXXXXX
export RUNTIME_SECRET_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

`ObjectStorageFullAccess` above is scoped to this one project; narrow it to the
`ObjectStorageObjects*` sets if you want least privilege and are willing to
test the upload path afterwards.

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

**Choosing `cpu-min`.** Set it to `0` while you are setting things up. Before
go-live, read the note on the notifier schedule in step 6 — the two decisions
interact, and picking them independently is how you end up paying for an idle
database.

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

## 3. Object Storage for uploaded images

Class cover images (`ActivityClass.image`) cannot live on the container
filesystem: it is ephemeral and per-instance, so an image uploaded through one
instance would 404 on the next request.

```sh
# Bucket names share one global namespace across all Scaleway users, so this
# will fail if someone else took it. Prefix it with your school's name.
BUCKET="$APP_NAME-media"
scw object bucket create "$BUCKET" region=fr-par
```

Objects are uploaded with a `public-read` ACL by `django-storages`, so they are
readable without a signed URL — which is what lets a CDN cache them. The bucket
itself can stay private.

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

First, the environment every one of them shares:

```sh
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
export SITE_URL="https://$DOMAIN"
```

### The web container

```sh
NS_ID=$(scw container namespace create name="$APP_NAME" -o json | jq -r '.id')

scw container container create namespace-id="$NS_ID" name="$APP_NAME-web" \
  registry-image="$IMAGE" \
  port=8080 \
  min-scale=0 max-scale=5 \
  memory-limit=1024 cpu-limit=1000 \
  scaling-option.concurrent-requests-threshold=8 \
  timeout=60s \
  privacy=public \
  http-option=redirected \
  health-check.http.path=/_health \
  health-check.interval=30s \
  environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod \
  environment-variables.ALLOWED_HOSTS="$DOMAIN" \
  environment-variables.CSRF_TRUSTED_ORIGINS="https://$DOMAIN" \
  environment-variables.SITE_URL="$SITE_URL" \
  environment-variables.TIME_ZONE=Europe/Malta \
  environment-variables.S3_BUCKET="$BUCKET" \
  environment-variables.S3_REGION=fr-par \
  environment-variables.EMAIL_HOST=smtp.tem.scw.cloud \
  environment-variables.EMAIL_PORT=587 \
  environment-variables.DEFAULT_FROM_EMAIL="School Activities <notifications@$DOMAIN>" \
  secret-environment-variables.0.key=SECRET_KEY \
  secret-environment-variables.0.value="$SECRET_KEY" \
  secret-environment-variables.1.key=DATABASE_URL \
  secret-environment-variables.1.value="$DATABASE_URL" \
  secret-environment-variables.2.key=S3_ACCESS_KEY_ID \
  secret-environment-variables.2.value="$RUNTIME_ACCESS_KEY" \
  secret-environment-variables.3.key=S3_SECRET_ACCESS_KEY \
  secret-environment-variables.3.value="$RUNTIME_SECRET_KEY" \
  secret-environment-variables.4.key=EMAIL_HOST_PASSWORD \
  secret-environment-variables.4.value="$SMTP_PASSWORD" \
  -o json
```

Why these numbers:

| Setting | Value | Reason |
|---|---|---|
| `scaling-option.concurrent-requests-threshold` | 8 | Matches `GUNICORN_THREADS` in `deploy/gunicorn.conf.py`. Set them together or the platform will queue work on an instance that has no thread free for it. (This replaces the deprecated `max-concurrency` flag.) |
| `max-scale` | 5 | 5 × 8 = 40 concurrent requests, far more than a school needs on enrolment day. Raise it *and* `DB_POOL_MAX_SIZE` together — every instance holds up to `DB_POOL_MAX_SIZE` connections, and the database's connection ceiling scales with its allocated compute. |
| `min-scale` | 0 | Scale to zero after 15 idle minutes. Set `1` if a cold start on the first morning request is unacceptable; it costs roughly a small always-on instance. |
| `memory-limit` | 1024 | Django plus Pillow. Below 512 MB image uploads get tight. |
| `timeout` | 60s | Must be ≥ `GUNICORN_TIMEOUT` so gunicorn is the one that gives up first and returns a real error. |
| `http-option` | `redirected` | The platform redirects HTTP→HTTPS at the edge, before a request costs you an instance. |

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
  -o json
```

No cron: this one only ever runs from the deploy pipeline.

### The notifier job

```sh
scw jobs definition create name="$APP_NAME-notifier" \
  image-uri="$IMAGE" \
  cpu-limit=500 memory-limit=1024 \
  job-timeout=600s \
  cron-schedule.schedule="*/5 * * * *" \
  cron-schedule.timezone="Europe/Malta" \
  startup-command.0=python startup-command.1=manage.py \
  args.0=run_notifier args.1=--drain args.2=--max-seconds args.3=240 \
  environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod \
  environment-variables.SITE_URL="$SITE_URL" \
  environment-variables.EMAIL_HOST=smtp.tem.scw.cloud \
  environment-variables.EMAIL_PORT=587 \
  environment-variables.DEFAULT_FROM_EMAIL="School Activities <notifications@$DOMAIN>" \
  environment-variables.SECRET_KEY="$SECRET_KEY" \
  environment-variables.DATABASE_URL="$DATABASE_URL" \
  environment-variables.EMAIL_HOST_PASSWORD="$SMTP_PASSWORD" \
  -o json
```

`--drain` keeps cycling until the outbox is empty, then exits. Without it a run
would move only `NOTIFIER_BATCH_SIZE` (20) rows, so a broadcast to 300 families
would trickle out one cron tick at a time. `--max-seconds 240` keeps a run
comfortably inside the 600-second job timeout.

**Choosing the schedule — this is the one that costs money.** Every run queries
the database, and the database only stops billing compute after five minutes of
silence. A `* * * * *` schedule therefore keeps it awake permanently and you
never see a cent of the scale-to-zero saving. Every five minutes lets it idle
between runs.

What you give up is delivery latency. Nothing in this app is
latency-sensitive: the tightest deadline is a waiting-list offer that expires
after 48 configurable hours. Five minutes is invisible to a parent and it is
the setting to start from. Go to one minute only if someone complains that
confirmation emails feel slow — and expect the database bill to change when you
do.

> **Secrets in job definitions.** The commands above put `DATABASE_URL` in a
> plain environment variable, which is readable by anyone who can read the job
> definition. For anything beyond a first deploy, store it in Secret Manager and
> reference it instead:
> ```sh
> scw jobs secret create job-definition-id="$JOB_ID" \
>   secrets.0.secret-manager-id="$SECRET_ID" \
>   secrets.0.env-var-name=DATABASE_URL
> ```
> Containers have this built in already — that is what
> `secret-environment-variables` above is.

## 7. Run the first migration and create an admin

```sh
MIGRATE_ID=$(scw jobs definition list name="$APP_NAME-migrate" -o json | jq -r '.[0].id')
RUN_ID=$(scw jobs definition start "$MIGRATE_ID" -o json | jq -r '.id')
scw jobs run wait "$RUN_ID"
```

`createsuperuser` is interactive, which a job is not. Override the migration
job's arguments for one run instead — `scw jobs definition start` takes
contextual `args` that apply to that run only, leaving the definition alone:

```sh
scw jobs definition start "$MIGRATE_ID" \
  args.0=shell args.1=-c \
  args.2="from django.contrib.auth import get_user_model; U=get_user_model(); U.objects.create_superuser(email='you@example.com', password='CHANGE-ME')"
```

Change that password at first login. The same trick runs any management
command against production — `seed_demo`, a data fix, a `dbshell` query.

## 8. Custom domain, TLS and CDN

```sh
CONTAINER_ID=$(scw container container list name="$APP_NAME-web" -o json | jq -r '.[0].id')
scw container container get "$CONTAINER_ID" -o json | jq -r '.domain_name'
```

Point a `CNAME` for `$DOMAIN` at that hostname, then:

```sh
scw container domain create container-id="$CONTAINER_ID" hostname="$DOMAIN"
```

Scaleway issues and renews a Let's Encrypt certificate automatically. This is
what Caddy used to do in the old VPS stack; there is nothing to configure.

Add an Edge Services pipeline in front for caching. Static files already carry
year-long immutable cache headers (hashed filenames via WhiteNoise's manifest
storage), and so do media objects, so the CDN absorbs almost all asset traffic
without ever waking an instance.

If you put Edge Services in front of the media bucket too, set
`S3_CUSTOM_DOMAIN` on the container to that hostname and Django will render
image URLs pointing at the CDN.

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

Push to `main` and `.github/workflows/deploy.yml` takes it from there: build for
`linux/amd64`, push, run migrations as a job and wait for them, repoint the
notifier job, redeploy the container, then smoke-test `/_health`.

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

**Backups.** Serverless SQL Database is backed up automatically. There is no
manual-backup button, so if you want an off-Scaleway copy, run `pg_dump` on a
schedule — a third Serverless Job with a cron writing to Object Storage is the
natural home for it.

**Cost knobs, roughly in order of impact:**

1. The notifier cron interval (it decides whether the database ever idles).
2. `min-scale` on the container.
3. `cpu-min` on the database.
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

**`ALLOWED_HOSTS` must include every hostname that reaches Django**, including
the generated `…functions.fnc.fr-par.scw.cloud` endpoint if you use it directly.
The health probe is exempt: `config/health.py` answers it before host validation
precisely so a misconfigured `ALLOWED_HOSTS` cannot take the instances down.

**Advisory locks are not guaranteed** on Serverless SQL Database. This app does
not use them — the capacity mutex in `apps/enrollments/services.py` is a
row-level `SELECT … FOR UPDATE` inside a transaction, which the pooler honours
because it pins the connection for the transaction's duration. If you ever
reach for `pg_advisory_lock`, it will appear to work and then quietly not.

**Redirect loops.** If `/` bounces forever, the platform is forwarding plain
HTTP without `X-Forwarded-Proto`. Set `SECURE_SSL_REDIRECT=false` on the
container and let `http-option=redirected` handle the redirect at the edge.
