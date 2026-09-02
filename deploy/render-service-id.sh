#!/usr/bin/env bash
#
# Print the Render service id for a service name.
#
#   RENDER_API_KEY=rnd_... deploy/render-service-id.sh extralessons-web
#
# Render's list endpoint matches names by prefix, so the result is filtered to
# an exact match and the script fails if that is not exactly one service. Used
# by deploy-render.yml so nobody has to copy srv-/crn- ids into GitHub, and by
# render-github-config.sh. Pass --url to print the service's public URL instead.

set -euo pipefail

API="${RENDER_API:-https://api.render.com/v1}"
: "${RENDER_API_KEY:?set RENDER_API_KEY (Account settings → API Keys)}"

FIELD=".id"
if [ "${1:-}" = "--url" ]; then
  FIELD=".serviceDetails.url // empty"
  shift
fi
NAME="${1:?usage: render-service-id.sh [--url] <service-name>}"

matches=$(curl -fsS "$API/services?name=$NAME&limit=20" \
    -H "Authorization: Bearer $RENDER_API_KEY" -H "Accept: application/json" \
  | jq -c --arg n "$NAME" '[.[].service | select(.name == $n)]')

case "$(jq 'length' <<<"$matches")" in
  1) jq -r ".[0] | $FIELD" <<<"$matches" ;;
  0) echo "no Render service named '$NAME' — has the Blueprint been created?" >&2; exit 1 ;;
  *) echo "several Render services named '$NAME'; delete the stray one" >&2; exit 1 ;;
esac
