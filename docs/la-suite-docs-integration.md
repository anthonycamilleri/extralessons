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

---

# If what you want is a wiki

Docs is a *collaborative editor* — two people typing in the same paragraph.
Most of what made it expensive above comes from that one feature: the Yjs
WebSocket server that pins the container to a single instance, the Redis it
needs for sessions and its broker, and a permission model built around
per-document sharing rather than around who someone is.

A wiki — a handbook, the policies, the how-to pages providers keep asking
about — needs none of that. The options split cleanly in two.

## The split that matters

Everything that is a separate product costs the same three things, whichever
one you pick: the OpenID Provider work in this app, a second container to run
and deploy, and a second permission model to keep in step with ours by hand.

Exactly one class of option avoids all three: a wiki that runs **inside this
Django app**, where `request.user` is already a parent, provider or school
admin, and `ActivityClass.objects.managed_by()` already answers who looks after
what.

## In-process — genuinely "another module"

**django-wiki** (PyPI package `wiki`, 0.13.0, July 2026) is a Django app, not a
service. Same container, same Serverless SQL database, same deploy. Its
dependencies are all pure Python — bleach, django-mptt, django-nyt,
django-sekizai, sorl-thumbnail, markdown, Pillow — so nothing new gets
provisioned and the idle bill does not move.

Its permission model is the reason to look at it. Each article carries an owner
(our `User`), a Django group, and unix-style `group_read`/`group_write`/
`other_read`/`other_write` flags that can be pushed down the page tree
(`src/wiki/models/article.py`). On top of that, `WIKI_CAN_READ`,
`WIKI_CAN_WRITE`, `WIKI_CAN_DELETE`, `WIKI_CAN_MODERATE` and `WIKI_CAN_ADMIN`
are **callables we supply** (`src/wiki/conf/settings.py`). So "providers may
read, admins may write, super admins may moderate" is a few lines against
`User.Role` — no claims, no sync, no fork. Attachments go through Django's
storage API, which means our `DatabaseStorage`/S3 switch already covers them.

Worth checking before committing: their support table lists Django 4.2, 5.0,
5.1 and 6.0 for 0.13.x and up to 5.2 for 0.12.x, and we are on 5.2 — an odd gap
that is probably a documentation omission, but confirm it. It also arrives with
Bootstrap templates that will not look like this site until they are overridden,
and with django-nyt, its own notification app, which sits awkwardly beside our
notification outbox and is best left switched off.

**Or write it.** We already have `markdown` and `nh3`, a rich-text editor with
image upload (`apps/notifications/richtext.py`), Markdown pages rendered from
the admin (`SiteConfig.terms_html`), and an admin the office already knows. A
`Page` model with a slug, a body and a visibility choice is a few hundred lines
in the house style, with no upstream to track and nothing to override. If the
ask is "somewhere to put the handbook and the policies", this is very likely the
right answer.

Either way the subdomain still works: attach a second custom domain to the same
container and switch `request.urlconf` on the Host header — the same shape as
`config/canonical.py`, a few lines.

## Separate products, lightest first

| | Runtime | Needs | Auth | Verdict |
|---|---|---|---|---|
| **Wiki.js** | one Node container | Postgres only — **no Redis**, no S3 (content lives in the database) | Generic OIDC with group mapping | Best standalone fit for this estate |
| **BookStack** | PHP 8.2 / Laravel 12 | **MySQL or MariaDB**, S3 optional | OIDC, SAML, LDAP | Excellent product, wrong database |
| **HedgeDoc** | one Node container | Postgres, uploads on local disk | OAuth2/OIDC, can gate on a roles claim | A collaborative pad, not a wiki |
| **Outline** | Node | Postgres **+ Redis** + S3 | OIDC | Same bill as Docs; BSL licence |
| **Docmost** | Node | Postgres **+ Redis** | OIDC | Same bill as Docs |

**Wiki.js** is the one to look at if the office wants a real wiki product with
its own editor and page tree: a single container beside ours, one more database,
no always-on Redis, and OIDC group mapping so the provider work we would have
done for Docs is not wasted. Its health is the caveat — 2.5.x is the stable line
(v2.5.310 is tagged), while the 3.0 rewrite on `main` has no release tag at all
after years in beta.

**BookStack** is arguably the nicest of these to actually use, MIT licensed,
with OIDC and SAML built in — but it is MySQL-only, so it means a managed MySQL
instance next to our PostgreSQL for one application.

**Outline** and **Docmost** reproduce the Docs bill — Postgres, always-on Redis,
websocket collaboration — without the argument that it is the French
government's and will be maintained for a decade.

## Recommendation

If this is a wiki: keep it in-process. django-wiki if a page tree, history and
per-article permissions are wanted; a small handbook app if it is really just
pages. Neither needs the OpenID Provider, neither adds a container, neither
moves the monthly bill, and both understand our roles natively.

Reach outside the app only if the office specifically wants a wiki *product* —
and then Wiki.js, accepting the provider work and one more container.
