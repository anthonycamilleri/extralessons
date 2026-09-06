#!/usr/bin/env bash
#
# One-shot provisioning of the Scaleway estate described in
# docs/scaleway-setup.md: two IAM applications, a Serverless SQL Database, a
# container registry, the web container, and the migrate and notifier jobs.
# Uploaded images live in the database (apps/media), so there is no bucket.
#
# It is idempotent and resumable. Every step looks for the resource before
# creating it, and everything it learns is appended to deploy/.scaleway-state
# (gitignored). That file matters: IAM secret keys are shown exactly once by
# the API, so if you lose it the only recovery is to create new keys.
#
#   cp deploy/scaleway.env.example deploy/scaleway.env   # edit it
#   ./deploy/provision.sh
#
# Re-run it as often as you like; completed steps are skipped and the
# environment of the container and jobs is converged to deploy/scaleway-env.lib.sh.
#
# For an estate that already exists, .github/workflows/scaleway-configure.yml
# does the environment part from GitHub Actions without this script.

set -euo pipefail

# Git Bash (MSYS) rewrites any argument that looks like a Unix absolute path
# into a Windows one, so `liveness-probe.http.path=/_health` reaches the API as
# `C:/Program Files/Git/_health`. The container then fails every health check
# and lands in `error` with no obvious cause. Unset on Linux and macOS, where
# this variable simply does nothing.
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
CONFIG="$HERE/scaleway.env"
STATE="$HERE/.scaleway-state"
# shellcheck disable=SC1091
. "$HERE/scaleway-env.lib.sh"

# --- Output -----------------------------------------------------------------

if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; D=""; N=""
fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
skip() { printf '    %s·%s %s %s(exists)%s\n' "$D" "$N" "$*" "$D" "$N"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# --- Preconditions ----------------------------------------------------------

command -v scw    >/dev/null || die "scw not found. https://github.com/scaleway/scaleway-cli"
command -v jq     >/dev/null || die "jq not found. https://jqlang.github.io/jq/"
command -v docker >/dev/null || die "docker not found."

[ -f "$CONFIG" ] || die "missing $CONFIG — copy deploy/scaleway.env.example and edit it."
# shellcheck disable=SC1090
. "$CONFIG"

: "${APP_NAME:?set APP_NAME in $CONFIG}"
# DOMAIN is optional. Left empty, the site runs on the endpoint Scaleway
# generates for the container, which is only knowable after the container
# exists — so the host-dependent settings are applied in a second pass below.
: "${DOMAIN:=}"
: "${APEX_DOMAIN:=}"
: "${ADMIN_EMAIL:=}"
: "${TIME_ZONE:=Europe/Malta}"
: "${REGION:=fr-par}"
: "${DEFAULT_FROM_EMAIL:=School Activities <notifications@${DOMAIN:-example.com}>}"
: "${ZEPTOMAIL_SEND_MAIL_TOKEN:=}"
: "${ZEPTOMAIL_API_URL:=https://api.zeptomail.eu/v1.1/email}"
: "${MIN_SCALE:=0}"
export TIME_ZONE DEFAULT_FROM_EMAIL ZEPTOMAIL_API_URL ADMIN_EMAIL ZEPTOMAIL_SEND_MAIL_TOKEN

export SCW_DEFAULT_REGION="$REGION"

# State is sourced *after* config so a resource we already made wins over any
# stale value someone typed into the config by hand.
[ -f "$STATE" ] && . "$STATE"
touch "$STATE"; chmod 600 "$STATE" 2>/dev/null || true

remember() { # remember NAME VALUE
  local name="$1" value="$2"
  printf '%s=%q\n' "$name" "$value" >> "$STATE"
  export "$name=$value"
}

scwj() { scw "$@" -o json; }

# `scw config get` only reads the config file, so an environment-variable
# setup (CI, or anyone who skipped `scw init`) has to be consulted separately.
scw_setting() { # scw_setting CONFIG_KEY ENV_VAR
  local v
  v="$(scw config get "$1" 2>/dev/null || true)"
  [ -n "$v" ] && [ "$v" != "<nil>" ] && { printf '%s' "$v"; return; }
  printf '%s' "${!2:-}"
}

PROJECT_ID="$(scw_setting default-project-id SCW_DEFAULT_PROJECT_ID)"
ORGANIZATION_ID="$(scw_setting default-organization-id SCW_DEFAULT_ORGANIZATION_ID)"
[ -n "$PROJECT_ID" ] || die "no default project. Run 'scw init', or export SCW_DEFAULT_PROJECT_ID (see docs/scaleway-setup.md)."

# Fail on a bad key here, with a sentence that says so, rather than fifteen
# lines later inside a jq pipeline that saw an error object instead of a list.
scwj iam application list page-size=1 >/dev/null 2>&1 \
  || die "Scaleway rejected these credentials. Check 'scw init' or SCW_ACCESS_KEY/SCW_SECRET_KEY."

printf '%sProvisioning %s in %s%s\n' "$B" "$APP_NAME" "$REGION" "$N"
printf '  project %s\n  domain  %s\n  state   %s\n' \
  "$PROJECT_ID" "${DOMAIN:-(Scaleway-generated endpoint)}" "$STATE"

# --- 1. IAM applications ----------------------------------------------------
# The app authenticates to the database as an IAM principal: the application ID
# is the Postgres username and its API secret key is the password. CI gets a
# separate application with no database or storage access at all.

find_app() { scwj iam application list name="$1" | jq -r ".[]? | select(.name==\"$1\") | .id" | head -1; }

step "IAM application: $APP_NAME-runtime"
if [ -z "${RUNTIME_APP_ID:-}" ]; then
  existing="$(find_app "$APP_NAME-runtime")"
  if [ -n "$existing" ]; then
    remember RUNTIME_APP_ID "$existing"; skip "$APP_NAME-runtime"
  else
    id="$(scwj iam application create name="$APP_NAME-runtime" \
      description="$APP_NAME running on Serverless" | jq -r '.id')"
    remember RUNTIME_APP_ID "$id"; ok "created $id"
    scwj iam policy create name="$APP_NAME-runtime" application-id="$id" \
      rules.0.project-ids.0="$PROJECT_ID" \
      rules.0.permission-set-names.0=ServerlessSQLDatabaseReadWrite >/dev/null
    ok "policy attached (SQL read/write)"
  fi
else
  skip "$APP_NAME-runtime ($RUNTIME_APP_ID)"
fi

if [ -z "${RUNTIME_SECRET_KEY:-}" ]; then
  key="$(scwj iam api-key create application-id="$RUNTIME_APP_ID" \
    default-project-id="$PROJECT_ID" description="$APP_NAME runtime")"
  remember RUNTIME_ACCESS_KEY "$(echo "$key" | jq -r '.access_key')"
  remember RUNTIME_SECRET_KEY "$(echo "$key" | jq -r '.secret_key')"
  ok "runtime API key created (secret saved to state file)"
else
  skip "runtime API key"
fi

step "IAM application: $APP_NAME-ci"
if [ -z "${CI_APP_ID:-}" ]; then
  existing="$(find_app "$APP_NAME-ci")"
  if [ -n "$existing" ]; then
    remember CI_APP_ID "$existing"; skip "$APP_NAME-ci"
  else
    id="$(scwj iam application create name="$APP_NAME-ci" \
      description="$APP_NAME GitHub Actions" | jq -r '.id')"
    remember CI_APP_ID "$id"; ok "created $id"
    scwj iam policy create name="$APP_NAME-ci" application-id="$id" \
      rules.0.project-ids.0="$PROJECT_ID" \
      rules.0.permission-set-names.0=ContainerRegistryFullAccess \
      rules.1.project-ids.0="$PROJECT_ID" \
      rules.1.permission-set-names.0=ContainersFullAccess \
      rules.2.project-ids.0="$PROJECT_ID" \
      rules.2.permission-set-names.0=ServerlessJobsFullAccess >/dev/null
    ok "policy attached (registry + containers + jobs, deliberately no DB or IAM)"
  fi
else
  skip "$APP_NAME-ci ($CI_APP_ID)"
fi

if [ -z "${CI_SECRET_KEY:-}" ]; then
  key="$(scwj iam api-key create application-id="$CI_APP_ID" \
    default-project-id="$PROJECT_ID" description="$APP_NAME CI")"
  remember CI_ACCESS_KEY "$(echo "$key" | jq -r '.access_key')"
  remember CI_SECRET_KEY "$(echo "$key" | jq -r '.secret_key')"
  ok "CI API key created"
else
  skip "CI API key"
fi

# --- 2. Serverless SQL Database ---------------------------------------------

step "Serverless SQL Database: $APP_NAME"
if [ -z "${DB_HOST:-}" ]; then
  existing="$(scwj sdb-sql database list name="$APP_NAME" \
    | jq -r ".[]? | select(.name==\"$APP_NAME\") | .endpoint" | head -1)"
  if [ -z "$existing" ] || [ "$existing" = "null" ]; then
    created="$(scwj sdb-sql database create name="$APP_NAME" cpu-min=0 cpu-max=4)"
    existing="$(echo "$created" | jq -r '.endpoint')"
    ok "created (cpu-min=0, scales to zero)"
  else
    skip "database $APP_NAME"
  fi
  # The endpoint comes back as a full URI; the connection string wants the host.
  host="$(echo "$existing" | sed -E 's#^[a-z+]+://##; s#[:/].*$##')"
  [ -n "$host" ] || die "could not determine database host from '$existing'"
  remember DB_HOST "$host"
  ok "host $host"
else
  skip "database ($DB_HOST)"
fi

DATABASE_URL="postgres://${RUNTIME_APP_ID}:${RUNTIME_SECRET_KEY}@${DB_HOST}:5432/${APP_NAME}?sslmode=require"
export DATABASE_URL

# --- 4. Container Registry --------------------------------------------------

step "Container registry namespace: $APP_NAME"
if [ -z "${REGISTRY_NAMESPACE:-}" ]; then
  existing="$(scwj registry namespace list name="$APP_NAME" \
    | jq -r ".[]? | select(.name==\"$APP_NAME\") | .name" | head -1)"
  if [ -n "$existing" ]; then
    skip "$APP_NAME"
  else
    scwj registry namespace create name="$APP_NAME" is-public=false >/dev/null
    ok "created $APP_NAME"
  fi
  remember REGISTRY_NAMESPACE "$APP_NAME"
else
  skip "$REGISTRY_NAMESPACE"
fi

REGISTRY_HOST="rg.${REGION}.scw.cloud"
IMAGE="${REGISTRY_HOST}/${REGISTRY_NAMESPACE}/${APP_NAME}:bootstrap"

# --- 5. Build and push the bootstrap image ----------------------------------
# The container cannot be created without an image that already exists, so the
# first one is pushed from here. Afterwards GitHub Actions owns this.

step "Bootstrap image"
if [ "${SKIP_BUILD:-false}" = "true" ]; then
  warn "SKIP_BUILD=true — assuming $IMAGE is already pushed"
else
  echo "$CI_SECRET_KEY" | docker login "$REGISTRY_HOST" -u nologin --password-stdin >/dev/null
  ok "logged in to $REGISTRY_HOST"
  # --platform is not optional: Serverless rejects arm64 images, and it rejects
  # them at deploy time rather than build time. provenance=false keeps the push
  # a single-image manifest rather than a manifest list.
  docker buildx build --platform linux/amd64 --target runtime \
    --provenance=false -t "$IMAGE" --push "$ROOT"
  ok "pushed $IMAGE"
fi

# --- 6. Shared application secrets ------------------------------------------

step "Application secrets"
# Both may be given in scaleway.env — copying SECRET_KEY from a previous host
# keeps every session and password-reset link valid across a move — and are
# generated otherwise. openssl is present wherever docker is.
if [ -z "${SECRET_KEY:-}" ]; then
  remember SECRET_KEY "$(openssl rand -base64 64 | tr -d '\n=+/' | cut -c1-64)"
  ok "generated Django SECRET_KEY"
else
  skip "SECRET_KEY"
fi
if [ -z "${MCP_API_TOKEN:-}" ]; then
  remember MCP_API_TOKEN "$(openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-48)"
  ok "generated MCP_API_TOKEN (the Claude connector's key; read it from the state file)"
else
  skip "MCP_API_TOKEN"
fi
export SECRET_KEY MCP_API_TOKEN
[ -n "$ZEPTOMAIL_SEND_MAIL_TOKEN" ] || warn "ZEPTOMAIL_SEND_MAIL_TOKEN empty — sends will fail and retry; the outbox keeps them"

# --- 7. The web container ---------------------------------------------------

step "Container namespace"
if [ -z "${CONTAINER_NAMESPACE_ID:-}" ]; then
  existing="$(scwj container namespace list name="$APP_NAME" \
    | jq -r ".[]? | select(.name==\"$APP_NAME\") | .id" | head -1)"
  if [ -n "$existing" ]; then
    remember CONTAINER_NAMESPACE_ID "$existing"; skip "$APP_NAME"
  else
    id="$(scwj container namespace create name="$APP_NAME" | jq -r '.id')"
    remember CONTAINER_NAMESPACE_ID "$id"; ok "created $id"
    # A namespace provisions its registry backing asynchronously; creating a
    # container against it too early fails.
    for _ in $(seq 1 30); do
      s="$(scwj container namespace get "$id" | jq -r '.status')"
      [ "$s" = "ready" ] && break
      sleep 4
    done
  fi
else
  skip "namespace ($CONTAINER_NAMESPACE_ID)"
fi

# The environment comes from deploy/scaleway-env.lib.sh, shared with the
# GitHub Actions configure workflow. The three host-dependent settings are not
# knowable until the container exists when no custom DOMAIN was given, so they
# are applied in a second pass below. `container update` replaces the plain
# environment map wholesale, which is why every pass sends the full set.
container_env() { # container_env GENERATED_HOST
  GENERATED_HOST="$1" scw_host_settings
  scw_plain_env; scw_container_secrets
  CONTAINER_ENV=("${PLAIN_ENV[@]}" "${CONTAINER_SECRETS[@]}")
}

step "Web container: $APP_NAME-web"
if [ -z "${CONTAINER_ID:-}" ]; then
  existing="$(scwj container container list name="$APP_NAME-web" \
    | jq -r ".[]? | select(.name==\"$APP_NAME-web\") | .id" | head -1)"
  if [ -n "$existing" ]; then
    remember CONTAINER_ID "$existing"; skip "$APP_NAME-web"
  else
    # A wrong ALLOWED_HOSTS here is survivable on purpose: config/health.py
    # answers /_health before host validation, so the container still reaches
    # "ready" and we can read its generated endpoint back off it.
    container_env "placeholder.invalid"
    # Argument names track scaleway-cli v2.61: `image` (not registry-image),
    # `memory-limit-bytes` in G/GB units (not memory-limit in MiB),
    # `mvcpu-limit` (not cpu-limit), `https-connections-only` (not
    # http-option), and `liveness-probe.*` (not health-check.*).
    #
    # concurrent-requests-threshold matches GUNICORN_THREADS in
    # deploy/gunicorn.conf.py — set the two together or the platform queues
    # work onto an instance with no free thread.
    id="$(scwj container container create \
      namespace-id="$CONTAINER_NAMESPACE_ID" name="$APP_NAME-web" \
      image="$IMAGE" port=8080 \
      min-scale="$MIN_SCALE" max-scale=5 \
      memory-limit-bytes=1GB mvcpu-limit=1000 \
      scaling-option.concurrent-requests-threshold=8 \
      timeout=60s privacy=public https-connections-only=true \
      liveness-probe.http.path=/_health liveness-probe.interval=30s \
      liveness-probe.timeout=5s liveness-probe.failure-threshold=3 \
      "${CONTAINER_ENV[@]}" \
      | jq -r '.id')"
    remember CONTAINER_ID "$id"; ok "created $id"
  fi
else
  skip "container ($CONTAINER_ID)"
fi

# The endpoint Scaleway generates is only readable once the container exists.
# Without a custom DOMAIN it is the hostname the site actually runs on, so
# ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS and SITE_URL have to be corrected now —
# Django would 400 every request that is not the exempt health probe.
step "Resolving the public hostname"
# The endpoint appears only once the container reaches `ready`, which means
# waiting out the first deployment — a failing health check keeps it in
# `creating` for a while and then flips it to `error`, so watch for both.
#
# It is exposed as `public_endpoint`, a full URL; older API versions carried a
# bare hostname in `domain_name`. Accept either and reduce to the hostname.
GENERATED_HOST=""
for _ in $(seq 1 60); do
  cjson="$(scwj container container get "$CONTAINER_ID")"
  cstatus="$(echo "$cjson" | jq -r '.status')"
  if [ "$cstatus" = "error" ]; then
    die "container failed to deploy: $(echo "$cjson" | jq -r '.error_message')"
  fi
  GENERATED_HOST="$(echo "$cjson" | jq -r '(.public_endpoint // .domain_name // "") | sub("^https?://";"")')"
  [ -n "$GENERATED_HOST" ] && [ "$cstatus" = "ready" ] && break
  sleep 5
done
[ -n "$GENERATED_HOST" ] || die "container has no endpoint after five minutes; check 'scw container container get $CONTAINER_ID'."
ok "endpoint $GENERATED_HOST"

container_env "$GENERATED_HOST"
scwj container container update "$CONTAINER_ID" "${CONTAINER_ENV[@]}" >/dev/null
remember WEB_HOST "$WEB_HOST"
ok "ALLOWED_HOSTS=$ALLOWED_HOSTS"
ok "SITE_URL=$SITE_URL"

# --- 8. The two jobs --------------------------------------------------------
# Jobs take plain environment-variables only; there is no secret-env equivalent
# on `jobs definition create`. See the Secret Manager note in
# docs/scaleway-setup.md for hardening this beyond a first deploy.

# Unlike every other list command here, `jobs definition list` takes no name=
# filter, so the whole list comes back and jq does the matching.
find_job() { scwj jobs definition list | jq -r --arg n "$1" '.[]? | select(.name==$n) | .id' | head -1; }

# Jobs take plain environment variables only. scw_job_env is the shared plain
# set plus the secrets the two jobs need; see the Secret Manager note in
# docs/scaleway-setup.md for keeping those out of the definition.
scw_job_env

step "Job: $APP_NAME-migrate"
if [ -z "${JOB_MIGRATE_ID:-}" ]; then
  existing="$(find_job "$APP_NAME-migrate")"
  if [ -n "$existing" ]; then
    remember JOB_MIGRATE_ID "$existing"; skip "$APP_NAME-migrate"
  else
    id="$(scwj jobs definition create name="$APP_NAME-migrate" \
      image-uri="$IMAGE" cpu-limit=500 memory-limit=1024 \
      local-storage-capacity=1024 job-timeout=600s \
      startup-command.0=python startup-command.1=manage.py \
      args.0=migrate args.1=--noinput \
      "${JOB_ENV[@]}" \
      | jq -r '.id')"
    remember JOB_MIGRATE_ID "$id"; ok "created $id (no cron; deploy-driven)"
  fi
else
  skip "migrate job ($JOB_MIGRATE_ID)"
fi
scwj jobs definition update "$JOB_MIGRATE_ID" "${JOB_ENV[@]}" >/dev/null
ok "environment converged"

step "Job: $APP_NAME-notifier"
if [ -z "${JOB_NOTIFIER_ID:-}" ]; then
  existing="$(find_job "$APP_NAME-notifier")"
  if [ -n "$existing" ]; then
    remember JOB_NOTIFIER_ID "$existing"; skip "$APP_NAME-notifier"
  else
    id="$(scwj jobs definition create name="$APP_NAME-notifier" \
      image-uri="$IMAGE" cpu-limit=500 memory-limit=1024 \
      local-storage-capacity=1024 job-timeout=600s \
      cron-schedule.schedule="0 3 * * *" cron-schedule.timezone="$TIME_ZONE" \
      startup-command.0=python startup-command.1=manage.py \
      args.0=run_notifier args.1=--drain args.2=--max-seconds args.3=240 \
      "${JOB_ENV[@]}" \
      | jq -r '.id')"
    remember JOB_NOTIFIER_ID "$id"; ok "created $id (nightly 03:00 $TIME_ZONE)"
  fi
else
  skip "notifier job ($JOB_NOTIFIER_ID)"
fi
scwj jobs definition update "$JOB_NOTIFIER_ID" "${JOB_ENV[@]}" >/dev/null
ok "environment converged"

# --- 9. First migration -----------------------------------------------------

step "Running the first migration"
if [ "${SKIP_MIGRATE:-false}" = "true" ]; then
  warn "SKIP_MIGRATE=true"
else
  # `jobs definition start` answers with {"job_runs":[{...}]}, not a bare run.
  run_id="$(scwj jobs definition start "$JOB_MIGRATE_ID" | jq -r '.job_runs[0].id // .id')"
  echo "    run $run_id — waiting..."
  scw jobs run wait "$run_id" >/dev/null 2>&1 || true
  state="$(scwj jobs run get "$run_id" | jq -r '.state // "unknown"')"
  if [ "$state" = "succeeded" ]; then
    ok "migrations applied"
    # Contextual args apply to this run only: same job, different command.
    # Creates the ADMIN_EMAIL superuser and emails it a set-password link;
    # finds it and does nothing on every later run.
    run_id="$(scwj jobs definition start "$JOB_MIGRATE_ID" args.0=ensure_admin | jq -r '.job_runs[0].id // .id')"
    scw jobs run wait "$run_id" >/dev/null 2>&1 || true
    [ "$(scwj jobs run get "$run_id" | jq -r '.state')" = "succeeded" ] \
      && ok "admin account ensured for ${ADMIN_EMAIL:-<ADMIN_EMAIL unset: skipped>}" \
      || warn "ensure_admin finished oddly; inspect with: scw jobs run get $run_id"
  else
    warn "migration run finished in state '$state'"
    warn "inspect with: scw jobs run get $run_id"
  fi
fi

# --- 10. What is left for a human -------------------------------------------

if [ -n "$DOMAIN" ]; then
  DNS_SECTION="$(cat <<EOF
${B}1. Point DNS at the container$N
   CNAME  $DOMAIN  ->  $GENERATED_HOST

   Once it resolves, attach the domain so Scaleway issues the certificate:
   scw container domain create container-id=$CONTAINER_ID hostname=$DOMAIN
   (APEX_DOMAIN: an ALIAS/ANAME record at the apex, then the same command for it)
EOF
)"
else
  DNS_SECTION="$(cat <<EOF
${B}1. The site is already live$N — no DNS needed yet.
   https://$GENERATED_HOST

   To move it to your own domain later, add a CNAME pointing at that
   hostname, then run these two commands (the second is what stops Django
   400-ing requests for the new name):
   scw container domain create container-id=$CONTAINER_ID hostname=YOUR.DOMAIN
   scw container container update $CONTAINER_ID \\
     environment-variables.ALLOWED_HOSTS=YOUR.DOMAIN,$GENERATED_HOST \\
     environment-variables.CSRF_TRUSTED_ORIGINS=https://YOUR.DOMAIN \\
     environment-variables.SITE_URL=https://YOUR.DOMAIN
   ...re-sending every other environment-variable too: an update replaces
   the whole map. Easier: set DOMAIN in $CONFIG and re-run this script.
EOF
)"
fi

cat <<EOF

$B────────────────────────────────────────────────────────────────$N
$B Provisioned.$N Secrets and IDs are in $STATE (keep it, do not commit it).

$DNS_SECTION

${B}2. Log in$N
   ${ADMIN_EMAIL:-ADMIN_EMAIL} has been emailed a set-password link (if ADMIN_EMAIL and the
   ZeptoMail token were set). Otherwise use "Forgotten your password?" once email
   works, or: scw jobs definition start $JOB_MIGRATE_ID args.0=ensure_admin args.1=--send-reset

   The Claude connector key is MCP_API_TOKEN in $STATE.

${B}3. GitHub Actions — Settings → Secrets and variables → Actions$N

   Secrets:
     SCW_ACCESS_KEY               $CI_ACCESS_KEY
     SCW_SECRET_KEY               (CI_SECRET_KEY in $STATE)
     SCW_DEFAULT_PROJECT_ID       $PROJECT_ID
     SCW_DEFAULT_ORGANIZATION_ID  ${ORGANIZATION_ID:-<scw config get default-organization-id>}

   Variables:
     SCW_REGISTRY_NAMESPACE       $REGISTRY_NAMESPACE
     SCW_CONTAINER_ID             $CONTAINER_ID
     SCW_JOB_MIGRATE_ID           $JOB_MIGRATE_ID
     SCW_JOB_NOTIFIER_ID          $JOB_NOTIFIER_ID
     APP_URL                      $SITE_URL

   With gh installed, deploy/github-config.sh writes all of these for you.

${B}4. Push to main$N — .github/workflows/deploy.yml takes over from here.
   Create the "production" environment under Settings → Environments first,
   or the workflow will refuse to start.
$B────────────────────────────────────────────────────────────────$N
EOF
