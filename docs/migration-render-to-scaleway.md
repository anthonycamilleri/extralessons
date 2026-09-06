# Moving production from Render back to Scaleway

The runbook for the move, written so that the mechanical parts run from GitHub
Actions and the parts that need a human — approving, changing DNS, checking
that the site feels right — are short and listed. Budget an evening: about
twenty minutes of your attention spread over two hours, most of it waiting.

## What moves, and how

| What | How it gets to Scaleway |
|---|---|
| Rows (families, children, classes, enrolments, notification log, terms text, site configuration) | `pg_dump` from Render, `pg_restore` into the Serverless SQL Database, verified table by table — *Scaleway: move production from Render* |
| Uploaded class images | They live in the database (`apps/media`), so they arrive with the rows. Nothing to copy. |
| `SECRET_KEY` | Copied from Render by *Scaleway: configure the estate*, so every parent stays logged in and every outstanding password-reset or invitation link keeps working |
| ZeptoMail token, MCP connector token | Copied the same way; email and the Claude connector work unchanged |
| The application image | Built and deployed by *Deploy to Scaleway* on every push to `main` that passes CI |
| DNS for `www.esljparents.eu` and `esljparents.eu` | **You**, at the registrar (step 6) |
| TLS certificates | Scaleway issues them once the names are attached — *Scaleway: attach domains* |

Things that do *not* need doing: re-uploading images, re-creating admin
accounts, rotating tokens, re-verifying the sending domain in ZeptoMail,
changing the Claude connector (its URL is the custom domain, which follows
DNS).

## The estate you are moving to

Nothing on Scaleway was torn down. The inspection run of 6 September found the
whole estate still in place, and the workflows reuse it rather than create a
second one:

| Resource | Name / id |
|---|---|
| Container namespace | `extralessons` |
| Web container | `extralessons-web`, `cef3d3c3-f1ca-4355-9511-964daa0bee0b`, endpoint `extralessons8c9979e8-extralessons-web.functions.fnc.fr-par.scw.cloud` |
| Migrate job | `extralessons-migrate`, `017dee1b-e8ab-4786-a7d8-3280518dca0d` |
| Notifier job | `extralessons-notifier`, `8ded96d9-c357-4a17-beef-d5f96a12caf4`, nightly 03:00 Europe/Malta |
| Registry | `rg.fr-par.scw.cloud/extralessons`, images tagged by commit (last: `64c089c`, 2 September) |
| Serverless SQL Database | `extralessons` — holds the data as it was on 2 September; replaced by the restore |
| GitHub | `SCW_*` secrets and `SCW_CONTAINER_ID`, `SCW_JOB_*_ID`, `SCW_REGISTRY_NAMESPACE`, `APP_URL` variables, all still set |

The old container still carries `S3_*` variables from the bucket era. The
configure workflow replaces the environment wholesale, and with `S3_BUCKET`
absent the app stores and serves uploads from the database, as it does on
Render today.

## Where things stand (6 September 2026)

Done, from this branch, with Render still serving parents:

- *Scaleway: configure the estate* applied: container and both jobs carry the
  production environment, with `SECRET_KEY`, the ZeptoMail token and
  `MCP_API_TOKEN` copied from Render.
- *Deploy to Scaleway* run from the branch: the current image is on the
  container and both jobs; migrations and `ensure_admin` ran.
- *Scaleway: move production from Render* in **rehearsal** mode, five runs. The
  first three found and fixed two Serverless SQL Database quirks (see *What can
  go wrong*); the last one passed every step: 29 tables, 2,061 rows and 1.75 MB
  of images verified identical, migrate job green, container redeployed,
  smoke test 0 failures on the generated endpoint.

So the Scaleway estate is a working, verified snapshot of production as of
19:42 UTC. Steps 1 to 4 below are therefore already satisfied for a first
look; what remains is your testing (against the generated endpoint), the
merge, and then steps 5 to 9. Re-run the rehearsal after merging if a day or
more has passed, so the snapshot you test is fresh.

## Order of operations

Every workflow below is under *Actions → (name) → Run workflow*. Run them from
`main` once this branch is merged; they all bind to the `production`
environment, where the Render API key lives.

### 1. Merge this branch

Merging swaps the pipelines: `deploy.yml` (Scaleway) now fires when CI passes
on `main`; `deploy-render.yml` becomes manual. So the merge itself is the
first deploy to Scaleway: CI runs, then *Deploy to Scaleway* builds the image,
runs the migrate job against the (stale) Scaleway database and rolls the
container. Watch it go green. Render is untouched and still serving parents.

### 2. Inspect (optional, read-only)

*Inspect hosting estate* prints both estates — ids, states, images, schedules,
the names of every environment variable, never a value. Run it if anything in
the table above needs confirming.

### 3. Configure the Scaleway estate

*Scaleway: configure the estate* with **dry_run ticked** (the default). Read
the plan it prints: the plain variables in full, the secrets as `<secret>`.
Then run it again with dry_run unticked. It reads `SECRET_KEY`,
`ZEPTOMAIL_SEND_MAIL_TOKEN`, `MCP_API_TOKEN`, `ADMIN_EMAIL` and the email
settings from Render's environment through the Render API, keeps the Scaleway
database credentials the estate already has, writes the lot to the container
and both jobs, waits for the container to redeploy, and smoke-tests the
generated endpoint.

`min_scale` is left as it is unless you set it. Render's `starter` instance
was always on; Scaleway at `min-scale=0` sleeps after fifteen idle minutes and
the first request afterwards pays a cold start of a few seconds (plus a couple
more if the database was idle for over five minutes). Set `1` if that is
unacceptable during term time; it is billed continuously.

### 4. Rehearse the data copy

*Scaleway: move production from Render* with **mode = rehearsal**. Render stays
live and untouched; its database is opened to the runner's IP for the duration
and closed again after, whatever happens. The Scaleway database ends up as a
snapshot of production, verified: row counts for every table against the
counts taken at dump time, total bytes of uploaded images, and the migration
ledger. Then the migrate job runs (a no-op if Render and Scaleway are on the
same commit), the container is redeployed so its instances drop the pooled
connections that still point at the tables the restore replaced (without this
they answer 500 until they reconnect), and the generated endpoint is
smoke-tested.

Now do the **testing** below against the generated endpoint. Everything you
see there is a copy; break it freely. Rehearse as many times as you like.

### 5. Cut over

Pick a quiet time — after 21:00 on a school night is fine; parents rarely
register then, and the Scaleway notifier's nightly run at 03:00 will pick up
anything queued. Then *Scaleway: move production from Render* with
**mode = cutover** and `cutover` typed in the confirm box. The workflow:

1. suspends the Render cron job, then the web service — instant, no deploy,
   and `mode = rollback` reverses it; Render shows its own "service
   suspended" page meanwhile;
2. waits until Render stops answering, then a little longer for in-flight
   requests to commit;
3. copies and verifies the database exactly as in the rehearsal;
4. runs the migrate job on Scaleway, redeploys the container and smoke-tests
   the generated endpoint;
5. prints the DNS records to change.

From step 1 to step 4 is typically five to ten minutes, during which the site
is down. If any step fails, the workflow stops with Render still suspended and
the Render database intact: run **mode = rollback** to resume Render (thirty
seconds) and nothing has been lost.

### 6. DNS — by hand

At the registrar for `esljparents.eu`:

```
www.esljparents.eu   CNAME         extralessons8c9979e8-extralessons-web.functions.fnc.fr-par.scw.cloud
esljparents.eu       ALIAS/ANAME   extralessons8c9979e8-extralessons-web.functions.fnc.fr-par.scw.cloud
```

An apex cannot be a CNAME. If the provider offers no ALIAS/ANAME record, use
its HTTP redirect feature to send `esljparents.eu` to
`https://www.esljparents.eu`, or move the zone to Scaleway Domains and DNS,
which does support ALIAS. Lower the TTL to 300 seconds beforehand if it is
long; raise it again a day later.

Until DNS moves, parents who reach Render see its suspended page. That is the
gap to keep short, and it is why the cutover happens at night.

### 7. Attach the domains

Once `dig +short www.esljparents.eu` shows the Scaleway hostname, *Scaleway:
attach domains*. Scaleway validates the record and issues a Let's Encrypt
certificate for each name within a few minutes; the workflow prints the status
and is safe to re-run until both say `ready`. (Scaleway refuses a name whose
DNS does not point at the container yet, so this cannot be done earlier.)

### 8. Check the real thing

```sh
deploy/smoke.sh https://www.esljparents.eu
MCP_API_TOKEN=... deploy/smoke.sh https://www.esljparents.eu   # also exercises the connector
```

Then the testing list below, this time on the real domain, with a real login.
The weekly TLS check workflow, if merged, covers the certificates from here on.

### 9. A week later: decommission Render

Keep Render suspended for a week as the fallback. Then, in the Render
dashboard: delete the web service, the cron job and the database (take a final
manual backup from the database's page first if you want an off-site copy —
the Scaleway database is backed up automatically, but a second copy costs
nothing). Delete the `production` environment's `RENDER_API_KEY` secret.
Finally remove `render.yaml`, `deploy-render.yml`, `deploy/render-*.sh` and
`docs/render-setup.md` from the repository, and the *Freeze Render* and
*rollback* parts of the migration workflow with them.

## Testing

Against the generated endpoint after a rehearsal, and against
`www.esljparents.eu` after the cutover. Fifteen minutes.

**Automated**

- [ ] `deploy/smoke.sh <url>` reports 0 failures.
- [ ] The migration workflow's *Copy and verify* step ended with "The target is a complete copy."

**As a parent** (use a real family account; nothing here sends email except where noted)

- [ ] Log in. On the real domain after cutover, an already-logged-in browser should *still* be logged in — that is the copied `SECRET_KEY` working.
- [ ] The family page lists the right children and registrations, with the week's lessons.
- [ ] A class page shows its image (this proves uploaded images came across and the storage backend is the database, not the old bucket).
- [ ] Filter the catalogue by day and age; open a class; start a registration and stop at the confirmation step.
- [ ] *Forgotten your password?* on the login page: the email arrives from `ESLJ Parents <info@esljparents.eu>` and its link opens on the new host. (Sends one email.)

**As the office** (admin account)

- [ ] `/admin/`: the Requests page, the class dashboard with its counts, a roster, the notification log with its history intact.
- [ ] *Site configuration* opens and saves without changes.
- [ ] Uploaded files (admin → Uploaded files) lists the images with previews.
- [ ] Approve a pending request on the **rehearsal** copy only, and check that the parent's email is logged as sent in the notification log. (Do not do this on the rehearsal copy if the request is real — the parent gets a real email about a copy that will be overwritten.)

**Claude connector**

- [ ] Ask the Extralessons connector for the school overview. After cutover it works unchanged; during a rehearsal it still points at Render.

**Notifier**

- [ ] The morning after cutover, *Serverless Jobs → extralessons-notifier → Runs* shows a succeeded 03:00 run.

## What can go wrong, and what to do

| Symptom | Cause | Do |
|---|---|---|
| Configure: "Render holds no value for SECRET_KEY" | The env group is not named `extralessons-shared` | Check the name in the Render dashboard; the workflow's `RENDER_ENV_GROUP_NAME` env |
| Migrate: "could not reach the Render database after four minutes" | Allow-list change not applied, or the Render database is on a plan that ignores it | Re-run; or add the runner IP the workflow printed by hand in the database's *Access Control* and re-run |
| Migrate: pg_restore exits non-zero | Something in the dump the managed database refuses | The log names the object. The workflow already skips schema and extension entries; anything else is new — fix and re-run, Render is untouched (rehearsal) or suspended (cutover) |
| Migrate: row counts differ | The source changed during the copy | Only possible in rehearsal (Render live). Harmless there; a cutover freezes first |
| Smoke: 400 on the catalogue | `ALLOWED_HOSTS` missing the hostname | Configure again; check `domain`/`apex_domain` inputs |
| Smoke: 500 on every database-backed page right after a copy, or the verifier says a table "does not exist" | The restore's session emptied `search_path`; the Serverless SQL Database pooler hands that backend, settings and all, to the next clients | `deploy/migrate-db.sh` now runs `RESET ALL` on fresh connections after the restore and qualifies its own queries; the workflow also redeploys the container. If it recurs: `psql "$DATABASE_URL" -c 'reset all'` a few times, then redeploy |
| Smoke: redirect loop | Platform forwarding HTTP without `X-Forwarded-Proto` | Set `SECURE_SSL_REDIRECT=false` on the container (scaleway-setup.md, *Things that will bite you*) |
| Attach domains: refused | DNS not yet pointing at the container, or still cached | Wait for the TTL; `dig +short` must show the Scaleway hostname |
| Emails not arriving after cutover | Token not copied, or ZeptoMail blocking the new sending IPs (it does not filter by IP, but check) | The notification log shows the error text per row; *Inspect* shows whether `ZEPTOMAIL_SEND_MAIL_TOKEN` is set on the container |
| Need Render back | Anything above during the cutover window | *Scaleway: move production from Render*, **mode = rollback**; DNS back to Render if it was already changed |

## Why it is built this way

**Suspend rather than a maintenance page.** Freezing Render by suspending the
services is instant, needs no deploy, and is undone by one API call. The
repository also gained a proper freeze switch — `MAINTENANCE_MODE=true`
answers every request but the health probe with a friendly 503
(`config/maintenance.py`) — which is the right tool the *next* time a database
has to be copied from a host that stays up. It is not used here because the
code carrying it is not on Render until it is deployed there, and deploying to
Render during the move is one moving part too many.

**GitHub Actions as the operator.** The Scaleway CI key and the Render API key
are already in the repository's `production` environment, and both platforms
have APIs that reach everything the move needs — including reading Render's
secret values, so `SECRET_KEY` and the tokens cross over without anyone
pasting them anywhere. The dump never leaves the runner and is deleted at the
end of the job: it holds families' personal data, and Render remains the
backup until you delete it.

**No object storage.** The previous Scaleway estate kept uploads in a bucket;
Render forced them into the database, and the database turned out to be the
right size for the job (a class image is a small JPEG; a whole catalogue is
tens of megabytes). Keeping it there means one provider fewer, one credential
fewer, and backups that include the pictures.

**Jobs carry their secrets in plain variables.** Serverless Jobs have no
secret-variable field; anyone who can read a job definition can read
`DATABASE_URL` and the email token. That was true of the old estate too, and it
is what lets the migration workflow find the database credentials without a
human. The tighter alternative — Secret Manager references — is described in
[scaleway-setup.md](scaleway-setup.md); do it once the move has settled.
