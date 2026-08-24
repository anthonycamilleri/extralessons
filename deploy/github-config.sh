#!/usr/bin/env bash
#
# Push the secrets and variables .github/workflows/deploy.yml needs into the
# GitHub repository, reading them from deploy/.scaleway-state.
#
# Run deploy/provision.sh first. Requires the gh CLI, authenticated:
#   gh auth login
#
#   ./deploy/github-config.sh              # write to the current repo
#   ./deploy/github-config.sh owner/repo   # or a named one

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$HERE/.scaleway-state"
CONFIG="$HERE/scaleway.env"

command -v gh >/dev/null || { echo "gh not found: https://cli.github.com/" >&2; exit 1; }
[ -f "$STATE" ] || { echo "no $STATE — run deploy/provision.sh first" >&2; exit 1; }

# shellcheck disable=SC1090
[ -f "$CONFIG" ] && . "$CONFIG"
# shellcheck disable=SC1090
. "$STATE"

REPO_ARG=()
[ $# -ge 1 ] && REPO_ARG=(--repo "$1")

ORG_ID="$(scw config get default-organization-id 2>/dev/null || true)"
PROJECT_ID="$(scw config get default-project-id 2>/dev/null || true)"

need() { # need NAME
  [ -n "${!1:-}" ] || { echo "missing $1 in $STATE" >&2; exit 1; }
}
need CI_ACCESS_KEY; need CI_SECRET_KEY; need REGISTRY_NAMESPACE
need CONTAINER_ID;  need JOB_MIGRATE_ID; need JOB_NOTIFIER_ID

set_secret() {
  # gh reads the value from stdin when --body is omitted, which keeps the
  # secret out of the process argument list. printf without a trailing newline
  # matters: gh would otherwise store the newline as part of the value.
  printf '%s' "$2" | gh secret set "$1" "${REPO_ARG[@]}" >/dev/null
  echo "  secret   $1"
}
set_var() {
  gh variable set "$1" "${REPO_ARG[@]}" --body "$2" >/dev/null
  echo "  variable $1 = $2"
}

echo "Writing GitHub configuration..."
set_secret SCW_ACCESS_KEY              "$CI_ACCESS_KEY"
set_secret SCW_SECRET_KEY              "$CI_SECRET_KEY"
set_secret SCW_DEFAULT_PROJECT_ID      "$PROJECT_ID"
set_secret SCW_DEFAULT_ORGANIZATION_ID "$ORG_ID"

set_var SCW_REGISTRY_NAMESPACE "$REGISTRY_NAMESPACE"
set_var SCW_CONTAINER_ID       "$CONTAINER_ID"
set_var SCW_JOB_MIGRATE_ID     "$JOB_MIGRATE_ID"
set_var SCW_JOB_NOTIFIER_ID    "$JOB_NOTIFIER_ID"
# WEB_HOST is written by provision.sh and is the hostname the site actually
# answers on — the custom domain if there is one, the generated endpoint if not.
[ -n "${WEB_HOST:-}" ] && set_var APP_URL "https://$WEB_HOST"

cat <<'EOF'

Done. Note deploy.yml pins the job to the "production" environment — if that
environment does not exist yet, create it under Settings → Environments, or
the workflow will not start.
EOF
