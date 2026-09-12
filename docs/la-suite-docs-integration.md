# Adding La Suite Docs as a module — feasibility

*Assessment written September 2026 against
[suitenumerique/docs](https://github.com/suitenumerique/docs) `main` and the
estate described in [scaleway-setup.md](scaleway-setup.md).*

The question was whether [Docs](https://docs.la-suite.eu/) — the collaborative
editor from La Suite numérique — could be bolted on as another module of this
app, under its own subdomain, on the hosting we already pay for, using the
accounts and roles we already have.

**Short answer: yes, but it is a second application, not a module.** The
subdomain is trivial, the hosting is affordable with three new pieces of
infrastructure, identity (single sign-on) is a day or two of real work, and
*authorisation* — "providers of Chess Club may edit this document" — does not
carry over at all without forking Docs.

## What Docs actually is

Not a Django app you can add to `INSTALLED_APPS`. It is a product with four
runtime pieces and three backing services:

| Piece | What it is |
|-------|------------|
| Backend | Django 5 + DRF (`src/backend`), run under uvicorn (ASGI) |
| Frontend | Next.js **static export** (`output: 'export'`) — plain files, no Node server |
| y-provider | Node/Express + Hocuspocus, the Yjs collaboration WebSocket server |
| nginx | Serves the static frontend, proxies `/api/` and `/admin/` to Django, `/collaboration/{api,ws}/` to y-provider, and `/media/` to S3 behind an auth subrequest |
| PostgreSQL | Its own database (needs `pg_trgm` and `unaccent`) |
| Redis | **Mandatory** — `SESSION_ENGINE` is the cache backend and `CACHES` in the `Production` configuration is Redis-only |
| S3 | Uploaded images and attachments; served through the nginx auth proxy |

Plus an **OpenID Connect provider**, which is not optional — see below.

The useful discovery is [`documentation/installation/scalingo.md`](https://github.com/suitenumerique/docs/blob/main/documentation/installation/scalingo.md)
and `bin/buildpack_start.sh`: upstream supports a single-container deployment
where nginx, uvicorn and y-provider run side by side in one process group. That
is the shape that fits our estate, rather than the Kubernetes chart.

## 1. Its own subdomain — trivial

`docs.esljparents.eu` is a CNAME at the registrar plus one command against the
new container, exactly as in [scaleway-setup.md §8](scaleway-setup.md).
Scaleway issues and renews the certificate.

Nothing in this app gets in the way:

* `CanonicalHostMiddleware` only redirects hosts listed in
  `CANONICAL_REDIRECT_HOSTS` (`config/canonical.py`), so a new subdomain is
  untouched.
* `SECURE_HSTS_INCLUDE_SUBDOMAINS`/`PRELOAD` are already on, which *requires*
  the subdomain to serve HTTPS — it will.
* Session cookies here are host-only (`SESSION_COOKIE_DOMAIN` is unset), so the
  two sites cannot tread on each other's sessions. With OIDC they do not need
  to share a cookie anyway.

## 2. Current hosting — yes, with three new bills and one hard ceiling

A second Serverless Container in the same namespace, built from the Docs image
(nginx + uvicorn + y-provider), is the right analogue of the Scalingo recipe.
Four things to know before committing:

**Redis is a new fixed cost.** There is no serverless Redis on Scaleway, so it
means a Managed Database for Redis running around the clock. This is the first
thing in the estate that cannot scale to zero, and it is not optional: Docs
keeps sessions and its theme cache there and uses it as the Celery broker.

**Collaboration pins the container to `max-scale=1`.** `y-provider`'s
dependencies contain no Hocuspocus Redis extension, so document state lives in
one process's memory. Two container instances means two people editing the same
document never see each other, and Scaleway Serverless Containers have no
session affinity to prevent that. One instance at concurrency 80 is far more
than the office needs, but it does mean the module cannot scale out, and a
crash drops everyone's live session (not their content — the SPA persists
through the Django API).

**Scale-to-zero fits an editor badly.** A WebSocket counts as an in-flight
request, so nobody is cut off mid-edit — but the 15-minute idle shutdown plus a
Docs image well over Scaleway's recommended 1 GB makes the first load of the
morning slow. `min-scale=1` fixes it and is the second always-on cost. Note
also that the platform caps a single request at 60 minutes, so a long editing
session's socket will be closed and reconnected; Hocuspocus handles that.

**Postgres is fine, with a caveat.** Serverless SQL Database supports both
extensions Docs creates (`pg_trgm`, `unaccent` are on Scaleway's supported
list), and nothing in Docs needs the features Scaleway's
[known differences](https://www.scaleway.com/en/docs/serverless-sql-databases/reference-content/known-differences/)
withhold (temp tables, guaranteed advisory locks). Give it its **own** database,
not tables next to ours. The same cold-start-after-five-idle-minutes behaviour
we already handle with pool health checks applies, and is more annoying here.

**Celery has no home.** Upstream's own single-app `Procfile` starts no worker,
so on that recipe `reset_service_connections_in_cascade`, ask-for-access
emails, search indexing and the marketing-contact task queue into Redis and
never run. The one that matters is the first: revoking someone's access will
not kick their live editing session until it reconnects. Fixing it properly is
a third container running `celery -A impress.celery_app worker` with
`min-scale=1`.

Email is the easy part: Docs uses plain Django SMTP, and the ZeptoMail relay
credentials work as-is. Our `ZeptoMailBackend` is our own code and would not
come along.

## 3. Users and permissions — identity yes, authorisation no

### Identity: we would have to become an OpenID Provider

Docs is OIDC-only in practice. The SPA's sole login path is
`${API}/authenticate/` (`src/frontend/.../features/auth/conf.ts`), which is
mozilla-django-oidc's login view; `ModelBackend` is still in
`AUTHENTICATION_BACKENDS` but only reachable through `/admin/`. There is no
"use my Django users" switch.

The clean answer is not to run Keycloak — that is a second always-on service
with its own database *and a second user store*, which is precisely what the
question was trying to avoid. It is to make **this** app the identity provider:
add `django-oauth-toolkit` with `OIDC_ENABLED`, an RS256 key and the discovery
document at `/o/.well-known/openid-configuration`, then point Docs'
`OIDC_OP_*` at `www.esljparents.eu`. Parents and staff keep one account and one
password; Docs creates its mirror user on first login, matched on `sub` or
`email`.

Two things to design for:

* The token and userinfo endpoints land in Docs' login path, so a cold start of
  *this* container becomes a slow login *there*. An argument for `min-scale=1`
  on the web container too, or at least for accepting it.
* Because we own the provider, we can decide **who is allowed in at all** by
  refusing the authorisation request for anyone whose `role` is not `ADMIN` or
  `PROVIDER`. Worth doing deliberately: otherwise every parent who logs in gets
  a Docs account and can create documents.

### Authorisation: our roles mean nothing to Docs

Docs' access model is per-document ACLs — `reader` / `editor` / `administrator`
/ `owner` — plus a link-reach setting, and it is complete and self-contained.
Nothing in it knows about parents, providers, classes, or which admin looks
after which class (`ActivityClass.objects.managed_by`).

The one hook that could bridge the two is `User.teams`, which Docs consults
when resolving document access. In upstream it is:

```python
@cached_property
def teams(self):
    """..."""
    return []
```

`src/backend/core/models.py:410` — a stub. La Suite's own deployment overrides
it. So "share this with everyone who administers Chess Club" means patching
Docs to read a `teams` claim off the userinfo response and cache it on the
user — i.e. maintaining a fork of a fast-moving upstream, forever, for the sake
of a convenience the office could also get by adding three people to a document
by hand.

**Recommendation:** ship SSO, and let document sharing be Docs' own per-document
sharing. Only take on the fork if group-derived access turns out to be the
actual reason for wanting this.

## What it would take

1. OIDC provider in this app (django-oauth-toolkit, RS256 key, claims for
   `email`/`full_name`, role gate on the authorize endpoint) — 1–2 days.
2. Build the Docs single-container image (their `Dockerfile` + the buildpack
   start script's process trio) and push it to our registry — 1 day.
3. Provision: second Serverless SQL database, Managed Redis, Object Storage
   bucket, container at `min-scale=1 max-scale=1`, migration job, optional
   Celery worker container — half a day on top of `deploy/provision.sh`.
4. Domain, theme customisation (`THEME_CUSTOMIZATION_JSON` for the school's
   logo and footer), and a deploy workflow alongside `deploy.yml` — 1 day.

Call it **1–2 weeks including testing**, and a monthly bill that is no longer
"almost nothing when idle": Redis plus one or two always-on container instances.

Before starting, worth confirming the cheaper option is genuinely unacceptable:
using a hosted instance with no SSO at all, and giving the office accounts
there. The entire cost and maintenance argument above buys exactly one thing —
that people sign in with the password they already have.
