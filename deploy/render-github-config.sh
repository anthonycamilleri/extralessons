#!/usr/bin/env bash
#
# Push the secret and variables .github/workflows/deploy-render.yml needs into
# the GitHub repository, looking the service IDs up from the Render API.
#
# Run after the Blueprint has been created (docs/render-setup.md). Requires the
# gh CLI, authenticated, and a Render API key:
#
#   gh auth login
#   RENDER_API_KEY=rnd_... ./deploy/render-github-config.sh              # current repo
#   RENDER_API_KEY=rnd_... ./deploy/render-github-config.sh owner/repo   # or a named one
#
# Service names default to the ones in render.yaml; override with
# WEB_SERVICE_NAME / CRON_SERVICE_NAME if you renamed them.

set -euo pipefail

API="${RENDER_API:-https://api.render.com/v1}"
: "${RENDER_API_KEY:?set RENDER_API_KEY (Account settings → API Keys)}"
WEB_SERVICE_NAME="${WEB_SERVICE_NAME:-extralessons-web}"
CRON_SERVICE_NAME="${CRON_SERVICE_NAME:-extralessons-notifier}"

command -v gh >/dev/null || { echo "gh not found: https://cli.github.com/" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

REPO_ARG=()
[ $# -ge 1 ] && REPO_ARG=(--repo "$1")

lookup() { # lookup NAME -> prints the service JSON, fails if not exactly one
  local name="$1" matches
  matches=$(curl -fsS "$API/services?name=$name&limit=20" \
      -H "Authorization: Bearer $RENDER_API_KEY" -H "Accept: application/json" \
    | jq -c --arg n "$name" '[.[].service | select(.name == $n)]')
  case "$(jq 'length' <<<"$matches")" in
    1) jq -c '.[0]' <<<"$matches" ;;
    0) echo "no Render service named '$name' — has the Blueprint been created?" >&2; exit 1 ;;
    *) echo "several Render services named '$name'; delete the stray one" >&2; exit 1 ;;
  esac
}

web=$(lookup "$WEB_SERVICE_NAME")
cron=$(lookup "$CRON_SERVICE_NAME")
WEB_ID=$(jq -r '.id' <<<"$web")
CRON_ID=$(jq -r '.id' <<<"$cron")
# The URL the service actually answers on: the custom domain once one is
# attached, the generated *.onrender.com one until then.
APP_URL=$(jq -r '.serviceDetails.url // empty' <<<"$web")

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
set_secret RENDER_API_KEY "$RENDER_API_KEY"
set_var RENDER_WEB_SERVICE_ID  "$WEB_ID"
set_var RENDER_CRON_SERVICE_ID "$CRON_ID"
[ -n "$APP_URL" ] && set_var APP_URL "$APP_URL"

cat <<'EOF'

Done. deploy-render.yml pins its job to the "production" environment — if that
environment does not exist yet, create it under Settings → Environments, or
the workflow will not start. Re-run this script after attaching a custom
domain so APP_URL follows it.
EOF
