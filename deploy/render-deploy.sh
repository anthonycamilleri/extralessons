#!/usr/bin/env bash
#
# Deploy one commit to one Render service and wait for the result.
#
#   RENDER_API_KEY=rnd_... deploy/render-deploy.sh <service-id> [commit-sha]
#
# Used by .github/workflows/deploy-render.yml for the web service and the
# notifier cron job in turn; works just as well from a laptop. Without a commit
# Render deploys the head of the service's linked branch. Passing an older
# commit is how you roll back — Render rebuilds it (or reuses its build cache)
# and the same health check gates the switch.
#
# Exit status is the deploy's: 0 only once Render reports it live.

set -euo pipefail

SERVICE_ID="${1:?usage: render-deploy.sh <service-id> [commit-sha]}"
COMMIT="${2:-}"
API="${RENDER_API:-https://api.render.com/v1}"
: "${RENDER_API_KEY:?set RENDER_API_KEY (Account settings → API Keys)}"
# Builds of this image take a few minutes; the pre-deploy migration and the
# health-checked switch add more. Thirty minutes is generous, not paranoid.
TIMEOUT_SECONDS="${RENDER_DEPLOY_TIMEOUT:-1800}"
POLL_SECONDS=10

api() { # api METHOD PATH [JSON-BODY]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -fsS -X "$method" "$API$path" \
      -H "Authorization: Bearer $RENDER_API_KEY" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl -fsS -X "$method" "$API$path" \
      -H "Authorization: Bearer $RENDER_API_KEY" \
      -H "Accept: application/json"
  fi
}

# Reading the service first turns a wrong ID or a revoked key into a readable
# error before anything is triggered.
service=$(api GET "/services/$SERVICE_ID")
name=$(jq -r '.name' <<<"$service")
kind=$(jq -r '.type' <<<"$service")
dashboard=$(jq -r '.dashboardUrl // empty' <<<"$service")
echo "Service: $name ($kind, $SERVICE_ID)"
[ -n "$dashboard" ] && echo "Dashboard: $dashboard"

if [ -n "$COMMIT" ]; then
  body=$(jq -cn --arg c "$COMMIT" '{clearCache: "do_not_clear", commitId: $c}')
  echo "Deploying commit $COMMIT"
else
  body='{"clearCache":"do_not_clear"}'
  echo "Deploying the head of the linked branch"
fi

deploy=$(api POST "/services/$SERVICE_ID/deploys" "$body")
deploy_id=$(jq -r '.id' <<<"$deploy")
echo "Deploy: $deploy_id"

deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
last=""
while :; do
  status=$(api GET "/services/$SERVICE_ID/deploys/$deploy_id" | jq -r '.status // "unknown"')
  if [ "$status" != "$last" ]; then
    echo "$(date -u +%H:%M:%S) $status"
    last="$status"
  fi
  case "$status" in
    live)
      echo "Live: $name is on deploy $deploy_id"
      exit 0
      ;;
    build_failed|update_failed|pre_deploy_failed|canceled|deactivated)
      echo "::error::Deploy of $name finished as '$status'. Logs: ${dashboard:-https://dashboard.render.com}" >&2
      exit 1
      ;;
  esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "::error::Deploy of $name still '$status' after ${TIMEOUT_SECONDS}s." >&2
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
