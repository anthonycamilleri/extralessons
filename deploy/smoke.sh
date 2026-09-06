#!/usr/bin/env bash
#
# Prove a deployment answers like production should, from the outside.
#
#   deploy/smoke.sh https://www.esljparents.eu
#   MCP_API_TOKEN=... deploy/smoke.sh https://xxxx.functions.fnc.fr-par.scw.cloud
#
# Checks the health probe, the public pages, the login and admin pages, that
# hashed static files are served with immutable caching, the security headers
# the platform is expected to preserve, and that /mcp refuses anonymous calls
# (and, given the token, answers a real tool call). Exit status is the number
# of failed checks, so it can gate a pipeline.

set -u
BASE="${1:?usage: deploy/smoke.sh https://host}"
BASE="${BASE%/}"
fails=0
if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; N=$'\033[0m'; else G=""; R=""; N=""; fi
pass() { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
fail() { printf '  %s✗%s %s\n' "$R" "$N" "$*"; fails=$((fails+1)); }

# fetch PATH [curl args...] -> sets CODE, HEADERS, BODY
fetch() {
  local path="$1"; shift
  local out; out="$(mktemp)"
  CODE="$(curl -sS -o "$out" -D "$out.h" -w '%{http_code}' --max-time 30 "$@" "$BASE$path" 2>/dev/null || echo 000)"
  HEADERS="$(tr -d '\r' < "$out.h" 2>/dev/null)"; BODY="$(cat "$out" 2>/dev/null)"
  rm -f "$out" "$out.h"
}
header() { printf '%s\n' "$HEADERS" | awk -v k="$1" 'BEGIN{IGNORECASE=1} tolower($1)==tolower(k)":" {sub(/^[^:]+: */, ""); print; exit}'; }
expect() { # expect "label" CODE
  if [ "$CODE" = "$2" ]; then pass "$1 → $CODE"; else fail "$1 → $CODE (wanted $2)"; fi
}

printf 'Smoke-testing %s\n' "$BASE"

fetch /_health;                        expect "GET /_health" 200
[ "$BODY" = "ok" ] && pass "/_health says ok" || fail "/_health body is '$BODY'"

fetch /;                               expect "GET / (catalogue)" 200
grep -q 'Activities' <<< "$BODY" && pass "catalogue renders" || fail "catalogue page has no 'Activities' in it"
[ -n "$(header strict-transport-security)" ] && pass "HSTS header present" || fail "no Strict-Transport-Security header"
[ "$(header x-frame-options)" = "DENY" ] && pass "X-Frame-Options: DENY" || fail "X-Frame-Options is '$(header x-frame-options)'"

css="$(grep -oE '/static/css/main\.[a-f0-9]+\.css' <<< "$BODY" | head -1)"
if [ -n "$css" ]; then
  fetch "$css";                        expect "GET $css (hashed static)" 200
  grep -qi immutable <<< "$(header cache-control)" && pass "static is immutable-cached" || fail "static Cache-Control is '$(header cache-control)'"
else
  fail "catalogue does not link a hashed main.css (collectstatic missing from the image?)"
fi

fetch /accounts/login/;                expect "GET /accounts/login/" 200
fetch /admin/login/;                   expect "GET /admin/login/" 200
fetch /terms/;                         [ "$CODE" = 200 ] || [ "$CODE" = 404 ] && pass "GET /terms/ → $CODE (404 = no T&Cs configured)" || fail "GET /terms/ → $CODE"
fetch /this-page-does-not-exist;       expect "GET unknown page" 404

fetch /mcp -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
if [ "$CODE" = 401 ]; then pass "POST /mcp without a key → 401"
elif [ "$CODE" = 404 ]; then pass "POST /mcp → 404 (MCP_API_TOKEN unset, endpoint off)"
else fail "POST /mcp without a key → $CODE (wanted 401)"; fi

if [ -n "${MCP_API_TOKEN:-}" ]; then
  fetch /mcp -X POST -H 'Content-Type: application/json' -H "X-API-Key: $MCP_API_TOKEN" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_overview","arguments":{}}}'
  expect "POST /mcp get_overview with the key" 200
  grep -q '"school_name"' <<< "$BODY" && pass "get_overview returned the school" || fail "get_overview returned no school_name"
fi

printf '\n%d check(s) failed.\n' "$fails"
exit "$fails"
