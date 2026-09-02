# Deploying Extralessons on Render

End-to-end setup, from a Render account to a deploying-on-green pipeline.
Everything runs in Render's `frankfurt` region, which keeps the data in the EU.

Budget about half an hour for the first run; most of it is waiting for builds.

## The short version

1. Install the Render GitHub App on `anthonycamilleri/extralessons`.
2. In ZeptoMail, verify the sending domain and copy the Mail Agent's Send
   Mail Token. Set `DEFAULT_FROM_EMAIL` in `render.yaml`, commit to `main`.
3. Dashboard → **New → Blueprint** → connect the repo → branch `main` →
   paste the ZeptoMail token when asked → **Deploy Blueprint**.
4. Create an API key, then `RENDER_API_KEY=… ./deploy/render-github-config.sh`
   to hand the service IDs to GitHub Actions.
5. Create the first admin from the web service's **Shell** tab.

From then on every push to `main` that passes CI deploys itself.

## How Render is organised

Render's vocabulary, since it decides where things live:

- **Workspace.** The account (or team). Billing, members and API keys hang
  off it. Create a KIC team workspace rather than deploying under a personal
  account, so access does not depend on one person.
- **Service.** One deployable thing: a web service, a cron job, a background
  worker, a static site. Each is linked to one repo and branch, or to a
  prebuilt image.
- **Postgres / Key Value.** Managed databases, separate from services.
- **Project → Environment.** Optional folders. A project groups the services
  and databases of one application; an environment inside it separates
  `production` from `staging`. Purely organisational, but worth using: it is
  what makes the dashboard readable once there are three or four resources.
- **Blueprint.** Infrastructure as code: a `render.yaml` in the repo that
  declares services, databases and environment groups. Creating a Blueprint
  instance from the dashboard creates everything in the file; every later push
  to the linked branch that changes the file syncs the resources to match.
  This repository's estate is a Blueprint, and `render.yaml` is the source of
  truth for it. Anything you change in the dashboard that the file also sets
  will be overwritten on the next sync — edit the file instead.

**Connecting a repository.** You can indeed add a GitHub repo directly from
the interface: *New → Web Service* (or Blueprint) lists the repositories the
Render GitHub App can see and you pick one. The first time, Render asks you to
install the app on your GitHub account or organisation; grant it access to
this repository only, not *All repositories*. That installation is also what
lets Render read the commit status ("checks pass") and post deploy statuses
back to GitHub.

The difference between the two *New* routes matters here:

- *New → Web Service* creates one service by hand, configured through forms.
  Fine for a throwaway, but the configuration then lives only in the
  dashboard.
- *New → Blueprint* reads `render.yaml` and creates all of it in one go —
  the web service, the cron job, the database, the shared environment group,
  the links between them. Use this one.

## The shape of it

```
   parents,           ┌──────────────────────────┐
   providers,   ───►  │  Render edge (TLS, CDN)  │  custom domain + certificate
   office             └────────────┬─────────────┘
                                   │
                      ┌────────────▼─────────────┐
                      │  Web service             │  Django under gunicorn.
                      │  "extralessons-web"      │  Static files from inside by
                      └────────────┬─────────────┘  WhiteNoise. Pre-deploy step
                                   │                runs the migrations.
              ┌────────────────────┼────────────────────┐
              │                    │                     │
   ┌──────────▼──────────┐  ┌──────▼───────────┐  ┌──────▼──────────────┐
   │ Render Postgres     │  │ S3-compatible    │  │ ZeptoMail API /      │
   │ "extralessons-db"   │  │ object storage   │  │ WhatsApp (outbound)  │
   └──────────▲──────────┘  │ (class images,   │  └──────────────────────┘
              │             │  your choice)    │
   ┌──────────┴──────────┐  └──────────────────┘
   │ Cron job            │  nightly `run_notifier --drain`
   │ "extralessons-notifier"
   └─────────────────────┘
```

Both services build the same `Dockerfile`, on Render, from the same commit.
The web service additionally has a **pre-deploy command**, `manage.py migrate`,
which Render runs in the freshly built image after the build and before the new
version takes any traffic. If it fails, the deploy is abandoned and the old
version keeps serving. This is the one feature that dictates the instance type:
pre-deploy commands need a paid instance (`starter` is the smallest), which is
why `render.yaml` does not use `free` anywhere.

**Why the notifier is a cron job and not a worker.** Delivery already happens
inline in the request that queued it. The cron job only covers what no click
can trigger — offer expiry, stuck-row recovery, retries due while nothing is
happening — and it exits as soon as the queue is empty. A cron job bills for
minutes a night; an always-on worker would bill for a month.

## Step by step

### 1. GitHub

Dashboard → *Workspace settings → GitHub* (or just start *New → Blueprint*,
which prompts for it) → install the Render GitHub App → select
`anthonycamilleri/extralessons` only.

### 2. Email: Zoho ZeptoMail

Email goes out through ZeptoMail's sending API
(`apps/notifications/backends/zeptomail.py`), not SMTP: one pooled HTTPS
request per message, structured error codes the notifier can act on, and
ZeptoMail's request id recorded on every delivery so a row in the admin can be
found in ZeptoMail's processed-emails log.

In ZeptoMail (`zeptomail.zoho.eu` for an EU account):

1. **Domains → Add domain** for the sending domain, and publish the DKIM and
   SPF records it gives you. Unverified domains cannot send.
2. **Mail Agents → Add**, one called `extralessons`. A Mail Agent is
   ZeptoMail's unit of configuration and reporting; one per application.
3. On the agent, **Setup info → API → Send Mail Token → Generate**. Copy it;
   this is the only secret.
4. Optionally add a bounce address under the agent's settings and set it as
   `ZEPTOMAIL_BOUNCE_ADDRESS` in the shared group.

Then in `render.yaml`, in the `extralessons-shared` group, set
`DEFAULT_FROM_EMAIL` to an address on the verified domain. `ZEPTOMAIL_API_URL`
defaults to the EU endpoint; a `.com` account needs
`https://api.zeptomail.com/v1.1/email`. Commit. Leave the custom domain and
object storage blocks commented out for now; they come later.

The token selects the backend: with it set, production uses the API; without
it, plain SMTP with the `EMAIL_*` variables (ZeptoMail's own relay at
`smtp.zeptomail.eu:587`, user `emailapikey`, password the same token, works
there too). `EMAIL_BACKEND` set explicitly overrides both.

### 3. Create the Blueprint

Dashboard → **New → Blueprint** → *Connect* next to the repository → name it
`extralessons`, branch `main` → Render shows the resources it is about to
create and asks for every `sync: false` value. There is one per service:
`ZEPTOMAIL_SEND_MAIL_TOKEN` on the web service and again on the cron job
(groups cannot hold hand-entered secrets, so it is asked twice; paste the same
token). **Deploy Blueprint.**

Render now creates the database, the environment group (generating
`SECRET_KEY`), and both services, then runs the first build and deploy of each.
The web service is live when its **Events** tab says so, at
`https://extralessons-web.onrender.com` (or `-xxxx.onrender.com` if the name
was taken; the dashboard shows the real one).

Nothing in `ALLOWED_HOSTS` had to be configured for this: Render passes the
generated hostname in as `RENDER_EXTERNAL_HOSTNAME` and
`config/settings/prod.py` picks it up.

### 4. Hand the pipeline to GitHub Actions

`render.yaml` sets `autoDeployTrigger: "off"` on both services, so Render does
**not** deploy on its own when `main` moves. `.github/workflows/deploy-render.yml`
does, after `CI` has passed on the commit. It needs:

| Kind | Name | Where from |
|---|---|---|
| secret | `RENDER_API_KEY` | Dashboard → avatar → *Account settings → API Keys → Create*. Scope it to the workspace |
| variable | `RENDER_WEB_SERVICE_ID` | The `srv-…` in the web service's URL |
| variable | `RENDER_CRON_SERVICE_ID` | The `crn-…` in the cron job's URL |
| variable | `APP_URL` | Optional. The URL the site answers on; enables the smoke test |

`deploy/render-github-config.sh` looks the IDs up by service name and writes
all four with the `gh` CLI:

```sh
gh auth login
RENDER_API_KEY=rnd_… ./deploy/render-github-config.sh
```

The workflow's job is pinned to the `production` GitHub environment. Create it
under *Settings → Environments* if it does not exist (adding required reviewers
there is how you get a manual approval step in front of production deploys, if
you ever want one).

**The alternative.** Render's own trigger can do the same gating with no
workflow: set `autoDeployTrigger: "checksPass"` on both services and Render
deploys each push to `main` whose GitHub checks are green, with rollback as a
button on the deploy. You lose the web-before-notifier ordering, the smoke
test and the GitHub environment gate, and gain one less moving part. Both are
defensible; the workflow is the default here because it is the shape the team
already knows.

### 5. The first admin user

Web service → **Shell** tab (paid instances have one), then:

```sh
python manage.py shell -c "from django.contrib.auth import get_user_model; U=get_user_model(); U.objects.create_superuser(email='you@example.com', password='CHANGE-ME')"
```

Log in at `/admin/`, change the password, then fill in *Site configuration*.

### 6. A custom domain

Web service → *Settings → Custom Domains → Add* → follow the DNS instructions
(a CNAME to the `onrender.com` hostname, or Render's A/ALIAS records for an
apex). Render issues and renews the certificate itself.

Then tell Django about it — in `render.yaml`, not the dashboard: uncomment
`domains:` and the three host variables (`ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `SITE_URL`) on the web service, set them to your
domain, commit. The Blueprint sync applies them. The generated hostname keeps
working alongside. Re-run `deploy/render-github-config.sh` so `APP_URL`
follows the domain.

### 7. Object storage for uploads

Render has no bucket product. Class images therefore need an S3-compatible
bucket somewhere else, and the settings are provider-neutral:

| Provider | `S3_ENDPOINT_URL` | `S3_REGION` | Notes |
|---|---|---|---|
| Scaleway Object Storage | `https://s3.fr-par.scw.cloud` | `fr-par` | Keep the existing bucket; the media stays where it is |
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` | `auto` | No egress fees; public access via a custom domain, set as `S3_CUSTOM_DOMAIN` |
| Backblaze B2 | `https://s3.eu-central-003.backblazeb2.com` | `eu-central-003` | Cheapest storage |
| AWS S3 | leave unset | `eu-central-1` | |

Uncomment the `S3_*` block on the web service in `render.yaml`, fill in the
literals, commit; enter the two keys in the dashboard when the sync asks (or
add them under *Environment* on the web service first). Until then uploads land
on the instance's disk and vanish on the next deploy — the app boots fine, so
this is not a blocker for the first deploy, only for going live with parents.

### 8. WhatsApp

Set `WHATSAPP_ENABLED` to `"true"` in the shared group, uncomment
`WHATSAPP_ACCESS_TOKEN` (secret) and `WHATSAPP_PHONE_NUMBER_ID` on both
services, commit.

## Operating it

**Deploying.** Push to `main`. *Actions → CI* runs; on green, *Deploy to
Render* runs and shows Render's deploy status as it moves through
`build_in_progress → pre_deploy_in_progress → update_in_progress → live`.
Render's own **Events** and **Logs** tabs show the build and migration output.

**Rolling back.** *Actions → Deploy to Render → Run workflow → `commit_sha`*
with an earlier commit: Render rebuilds it (usually from cache) and the same
health check gates the switch. Or, in the dashboard, *Rollback* on any earlier
deploy of the web service. Neither un-applies migrations — if the bad deploy
included a destructive migration you are restoring from a backup instead,
which is the usual reason to keep migrations additive and ship them a deploy
ahead of the code that needs them.

**Logs.** Everything goes to stdout; the **Logs** tab streams and searches
it, and *Log Streams* in workspace settings can forward it elsewhere. The
health probe is filtered out of the access log on purpose.

**The notifier.** Cron job → **Events** shows each nightly run and its exit
code; **Logs** its output. *Trigger Run* runs it now. The schedule is UTC —
`0 2 * * *` is 03:00 in Malta in winter and 04:00 in summer.

**Backups.** Render Postgres takes daily backups on all paid plans and keeps
them for the plan's retention window; point-in-time recovery is on the `pro`
plans. For an off-Render copy, `pg_dump` from a laptop against the external URL
(add your IP to `ipAllowList` in `render.yaml` first) is the simplest thing
that works.

**Scaling.** The web service scales by instance count and instance type, both
in `render.yaml` (`plan`, `numInstances`). Keep `DB_POOL_MAX_SIZE × instances`
comfortably under the database plan's connection limit; the gunicorn config
holds one process with eight threads per instance, and eight concurrent
requests per instance is a lot for this application.

**Cost knobs, in order:** the web instance type; the database plan; then
nothing else worth the effort — the cron job runs for minutes a night.

## Moving the data from Scaleway

The schema is identical; only the rows move.

```sh
# From a laptop with psql tools, Scaleway credentials in DATABASE_URL:
pg_dump --no-owner --no-privileges --format=custom "$SCALEWAY_DATABASE_URL" > extralessons.dump

# Add your IP to ipAllowList in render.yaml (commit, wait for the sync), then
# take the External Database URL from the Render dashboard:
pg_restore --no-owner --no-privileges --clean --if-exists \
  --dbname "$RENDER_EXTERNAL_DATABASE_URL" extralessons.dump
```

Do this after the first Render deploy has run the migrations (so the schema
exists) and before parents are pointed at the new host. Copy the media bucket
only if you are changing storage provider; if you keep Scaleway Object Storage,
set the same `S3_*` values on the web service and nothing moves.

Then: flip the DNS record, watch the Render logs for a day, and tear the
Scaleway estate down. `deploy/provision.sh` and
[scaleway-setup.md](scaleway-setup.md) stay in the repo until that is done.

## Limits worth knowing

- **Pre-deploy commands need a paid instance.** So does the Shell tab.
- **Cron schedules are UTC** and a cron job has no `free` plan.
- **No object storage on Render.** Persistent disks exist but pin a service
  to one instance and block zero-downtime deploys; a bucket is the right tool.
- **Blueprint sync deploys.** A push that changes `render.yaml` (a new env
  var, a plan change) redeploys the affected service on its own, outside the
  CI gate. Keep such commits separate from code changes.
- **Environment groups** hold literal values and generated secrets only. A
  hand-entered secret (`sync: false`) or a database reference has to sit on
  each service that needs it, which is why `ZEPTOMAIL_SEND_MAIL_TOKEN`
  appears twice.
- **ZeptoMail is transactional-only.** Its terms forbid marketing mail; a
  broadcast to all families about a class change is fine, a newsletter is
  not.
- **Immutable fields.** A service's `type` and `runtime`, and a database's
  `name`, `databaseName`, `user`, `region` and major version cannot change
  after creation; changing them in `render.yaml` means a new resource.
