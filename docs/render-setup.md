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
4. Create a Render API key and add it to the repository as the
   `RENDER_API_KEY` Actions secret; create the `production` environment.
5. Open the set-password email the first deploy sends to `ADMIN_EMAIL`.

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
   ┌──────────▼──────────┐                        ┌──────▼──────────────┐
   │ Render Postgres     │  rows + uploaded class │ ZeptoMail API /      │
   │ "extralessons-db"   │  images (apps/media)   │ WhatsApp (outbound)  │
   └──────────▲──────────┘                        └──────────────────────┘
              │
   ┌──────────┴──────────┐
   │ Cron job            │  nightly `run_notifier --drain`
   │ "extralessons-notifier"
   └─────────────────────┘
```

Everything lives on Render: two services and one database, no third-party
storage.

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
3. On the agent, **SMTP / API → API → Send Mail token**. Copy it; this is the
   only secret. The copy button copies `Zoho-enczapikey <token>`, the whole
   Authorization header; the backend accepts that or the bare token.
4. Optionally add a bounce address under the agent's settings and set it as
   `ZEPTOMAIL_BOUNCE_ADDRESS` in the shared group.

Then in `render.yaml`, in the `extralessons-shared` group, set
`DEFAULT_FROM_EMAIL` to the verified sender address (currently
`anthony@knowledgeinnovation.eu`; the agent's *Domain / Sender Address* field
shows what is allowed). `ZEPTOMAIL_API_URL` defaults to the EU endpoint, which
matches the `api.zeptomail.eu` host the agent shows; a `.com` account needs
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
does, after `CI` has passed on the commit. It needs one secret:

| Kind | Name | Where from |
|---|---|---|
| secret | `RENDER_API_KEY` | Dashboard → avatar → *Account settings → API Keys → Create*. Scope it to the workspace |

Add it under *Settings → Secrets and variables → Actions*. The workflow finds
the two services by their `render.yaml` names through the API
(`deploy/render-service-id.sh`) and reads the site URL from the web service, so
nothing else has to be copied. Three optional variables override that lookup:
`RENDER_WEB_SERVICE_ID`, `RENDER_CRON_SERVICE_ID` and `APP_URL`.
`deploy/render-github-config.sh` writes all four with the `gh` CLI if you
prefer:

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

Nothing to run. The pre-deploy command is `migrate` followed by
`manage.py ensure_admin`, which creates the account named by `ADMIN_EMAIL` in
the shared group (a superuser with a random, unknown password) the first time
it runs, and emails that address the same set-password link the login page's
*Forgotten your password?* produces. Check the inbox after the first deploy,
follow the link, choose a password, log in at `/admin/` and fill in *Site
configuration*.

If no email arrived (the ZeptoMail token was not in place yet, say), the
account still exists: use *Forgotten your password?* on the login page once
email works, or resend from the web service's **Shell** tab with
`python manage.py ensure_admin --send-reset`. The command is idempotent and
runs on every deploy; changing `ADMIN_EMAIL` later creates a second admin
rather than renaming the first. Add further administrators from the admin.

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

### 7. Uploaded images

Nothing to set up: in production, uploaded class images are stored in the
Postgres database and served by the web service at `/media/<name>` with a
one-year immutable cache header. This is `apps/media`, a small Django storage
backend, and it exists because the alternatives on Render are worse fits:

- **Static sites** serve files produced by a build. They cannot take uploads
  at run time.
- **Persistent disks** would work, but a disk pins the service to a single
  instance and removes zero-downtime deploys (the instance is stopped before
  its replacement starts), and the pre-deploy migration step cannot see it.
- **A bucket** means a second provider, which is what this avoids.

The database is the right size for the job. Every upload is shrunk to a JPEG of
at most 1600px before it is stored (`apps/catalog/images.py`), so a class image
is one or two hundred kilobytes; a school's whole catalogue is tens of
megabytes, inside the smallest database plan, and covered by its backups.

Replacing an image leaves the old row behind on purpose (its URL may still be
cached or open somewhere). Reclaim the space now and then from the web
service's Shell tab:

```sh
python manage.py prune_stored_files --dry-run   # list orphans
python manage.py prune_stored_files             # delete those older than a day
```

Stored files are listed, with a preview, under *Uploaded files* in the admin.

If the catalogue ever outgrows this — thousands of images, or files that are
not images — the S3 path is still there: set `S3_BUCKET` and friends on the web
service (the commented block in `render.yaml`) and the storage switches over.
Existing rows stay served by the database until re-uploaded.

### 8. Connect Claude to the catalogue

The web service exposes the catalogue tools as a remote MCP server at `/mcp`,
so Claude Desktop, Cowork and claude.ai can populate school years, terms,
providers and classes directly against the hosted app. Render generated the
token when the Blueprint was created.

1. Web service → **Environment** → copy the value of `MCP_API_TOKEN`.
2. In Claude: **Settings → Connectors → Add custom connector**. Name
   `Extralessons`; URL `https://extralessons-web.onrender.com/mcp` (the custom
   domain, once attached). Under **Request headers** add `Authorization:
   Bearer <token>`. No OAuth fields. Add.
3. In a chat or Cowork task, enable the connector and ask for the school
   overview.

The endpoint is a plain Django view speaking MCP's stateless Streamable HTTP
(`apps/catalog/mcp_http.py`); the tools are the same ones the stdio server
offers. The token grants everything they can do, including publishing classes
to parents. To rotate it: clear the value in the dashboard, trigger a Blueprint
sync (any push to `main` that touches `render.yaml`), then update the
connector. The README's *Connecting Claude* section has the local, stdio
alternative for development.

### 9. WhatsApp

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
exists) and before parents are pointed at the new host. Class images on the
Scaleway bucket do not come across with the rows: either re-upload them from
the admin (the `image` field simply points at a name that no longer resolves
until then), or keep the bucket for now by setting the `S3_*` variables on the
web service and move to database storage later, image by image.

Then: flip the DNS record, watch the Render logs for a day, and tear the
Scaleway estate down. `deploy/provision.sh` and
[scaleway-setup.md](scaleway-setup.md) stay in the repo until that is done.

## Limits worth knowing

- **Pre-deploy commands need a paid instance.** So does the Shell tab.
- **Cron schedules are UTC** and a cron job has no `free` plan.
- **No object storage on Render.** Uploads live in the database (step 7);
  persistent disks exist but pin a service to one instance, block
  zero-downtime deploys, and are invisible to the pre-deploy command.
- **Database plan storage.** `basic-256mb` comes with a small SSD allowance
  that images count against; the dashboard shows usage, and disk size can be
  raised in `render.yaml` without downtime.
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
