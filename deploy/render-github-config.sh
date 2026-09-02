#!/usr/bin/env bash
#
# Push the Render API key, and optionally the service ids and site URL, into
# the GitHub repository for .github/workflows/deploy-render.yml.
#
# Only the RENDER_API_KEY secret is required: the workflow looks the web
# service up by name at run time. The variables written here are overrides
# that save it an API call, and APP_URL pins the smoke test to a custom domain.
#
# Run after the Blueprint has been created (docs/render-setup.md). Requires the
# gh CLI, authenticated, and a Render API key:
#
#   gh auth login
#   RENDER_API_KEY=rnd_... ./deploy/render-github-config.sh              # current repo
#   RENDER_API_KEY=rnd_... ./deploy/render-github-config.sh owner/repo   # or a named one
#
# The service name defaults to the one in render.yaml; override with
# WEB_SERVICE_NAME if you renamed it.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${RENDER_API_KEY:?set RENDER_API_KEY (Account settings → API Keys)}"
WEB_SERVICE_NAME="${WEB_SERVICE_NAME:-extralessons-web}"

command -v gh >/dev/null || { echo "gh not found: https://cli.github.com/" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

REPO_ARG=()
[ $# -ge 1 ] && REPO_ARG=(--repo "$1")

WEB_ID=$("$HERE/render-service-id.sh" "$WEB_SERVICE_NAME")
# The URL the service actually answers on: the custom domain once one is
# attached, the generated *.onrender.com one until then.
APP_URL=$("$HERE/render-service-id.sh" --url "$WEB_SERVICE_NAME")

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
set_var RENDER_WEB_SERVICE_ID "$WEB_ID"
[ -n "$APP_URL" ] && set_var APP_URL "$APP_URL"

cat <<'EOF'

Done. deploy-render.yml pins its job to the "production" environment — if that
environment does not exist yet, create it under Settings → Environments, or
the workflow will not start. Re-run this script after attaching a custom
domain so APP_URL follows it (or delete the APP_URL variable and the workflow
will ask Render for the current URL each time).
EOF
