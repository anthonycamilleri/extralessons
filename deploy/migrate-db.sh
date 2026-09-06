#!/usr/bin/env bash
#
# Copy the production database from one PostgreSQL host to another and prove
# the copy is complete. Written for the move from Render back to Scaleway
# Serverless SQL Database, but it is plain pg_dump/pg_restore and works between
# any two PostgreSQL databases the machine running it can reach.
#
#   SOURCE_DATABASE_URL=postgres://...   # where the rows are now (Render: the
#                                        # *External* Database URL)
#   TARGET_DATABASE_URL=postgres://...   # where they are going (Scaleway: the
#                                        # DATABASE_URL provision.sh assembled)
#
#   deploy/migrate-db.sh                 # dump, restore, verify
#   deploy/migrate-db.sh --dump-only     # rehearsal / off-site backup
#   deploy/migrate-db.sh --restore-only deploy/dumps/extralessons-....dump
#   deploy/migrate-db.sh --verify-only   # compare row counts, change nothing
#
# Uploaded class images live in the database (apps/media), so they travel with
# the rows: there is no bucket to sync. Sequences, the django_migrations table
# and every constraint come across too — the target is the source, byte for
# byte, apart from ownership (dropped: the two hosts have different users).
#
# Freeze the source first (MAINTENANCE_MODE=true on the running site) or the
# copy is only as complete as the moment the dump started.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUMPS="$HERE/dumps"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; else B=""; G=""; R=""; Y=""; N=""; fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

MODE=full
DUMP_FILE=""
case "${1:-}" in
  "") ;;
  --dump-only)    MODE=dump ;;
  --verify-only)  MODE=verify ;;
  --restore-only) MODE=restore; DUMP_FILE="${2:-}"; [ -f "$DUMP_FILE" ] || die "--restore-only needs an existing dump file" ;;
  -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown option $1 (try --help)" ;;
esac

for tool in psql pg_dump pg_restore; do
  command -v "$tool" >/dev/null || die "$tool not found — install the PostgreSQL 16 client tools"
done

SRC="${SOURCE_DATABASE_URL:-}"
TGT="${TARGET_DATABASE_URL:-}"
[ "$MODE" = restore ] || [ -n "$SRC" ] || die "SOURCE_DATABASE_URL is not set"
[ "$MODE" = dump ]    || [ -n "$TGT" ] || die "TARGET_DATABASE_URL is not set"

# A DATABASE_URL that forgot sslmode still gets an encrypted connection.
with_ssl() { case "$1" in *sslmode=*) printf '%s' "$1" ;; *\?*) printf '%s&sslmode=require' "$1" ;; *) printf '%s?sslmode=require' "$1" ;; esac; }
[ -n "$SRC" ] && SRC="$(with_ssl "$SRC")"
[ -n "$TGT" ] && TGT="$(with_ssl "$TGT")"

# --- Helpers ---------------------------------------------------------------

sql() { # sql URL "query" -> tuples, no headers
  psql "$1" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 -c "$2"
}

server_version() { sql "$1" "show server_version" | tr -d ' '; }

# Exact row counts of every ordinary table in the public schema, one
# "table<TAB>count" per line, sorted, so the two sides can be diffed.
row_counts() { # row_counts URL
  local url="$1" tables t q=""
  tables="$(sql "$url" "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1")"
  [ -n "$tables" ] || { warn "no tables found"; return; }
  while IFS= read -r t; do
    [ -n "$t" ] || continue
    q="${q:+$q union all }select '$t' as t, count(*) as n from public.\"$t\""
  done <<< "$tables"
  sql "$url" "$q order by 1" | awk -F'|' '{printf "%s\t%s\n", $1, $2}'
}

# Bytes of uploaded images, so a truncated bytea would show even with matching
# row counts. Absent table (very old schema) = 0.
media_bytes() { # media_bytes URL
  sql "$1" "select coalesce(sum(size),0) from public.media_storedfile" 2>/dev/null || echo 0
}

# --- Preflight -------------------------------------------------------------

step "Preflight"
if [ -n "$SRC" ]; then
  sv="$(server_version "$SRC")" || die "cannot connect to the source database"
  ok "source  PostgreSQL $sv"
  dv="$(pg_dump --version | awk '{print $3}')"
  if [ "${dv%%.*}" -lt "${sv%%.*}" ]; then
    die "pg_dump $dv is older than the source server ($sv); install matching client tools"
  fi
fi
if [ -n "$TGT" ]; then
  tv="$(server_version "$TGT")" || die "cannot connect to the target database"
  ok "target  PostgreSQL $tv"
fi

# --- Dump ------------------------------------------------------------------

if [ "$MODE" = full ] || [ "$MODE" = dump ]; then
  step "Dumping the source"
  mkdir -p "$DUMPS"; chmod 700 "$DUMPS" 2>/dev/null || true
  DUMP_FILE="$DUMPS/extralessons-$(date -u +%Y%m%d-%H%M%SZ).dump"
  # Ownership and grants belong to the source host's user, which does not
  # exist on the target. Custom format is what pg_restore can filter and
  # reorder; --no-comments drops "COMMENT ON EXTENSION" lines a managed
  # database refuses.
  pg_dump --format=custom --no-owner --no-privileges --no-comments \
    --file="$DUMP_FILE" "$SRC"
  ok "$DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"
  # The counts at dump time are the yardstick for the restore, even if the
  # source keeps changing afterwards (it should not: freeze it first).
  row_counts "$SRC" > "$DUMP_FILE.counts"
  media_bytes "$SRC" > "$DUMP_FILE.media"
  ok "$(wc -l < "$DUMP_FILE.counts" | tr -d ' ') tables, $(awk -F'\t' '{s+=$2} END {print s+0}' "$DUMP_FILE.counts") rows, $(cat "$DUMP_FILE.media") bytes of uploaded images"
  [ "$MODE" = dump ] && { printf '\nDump only. Restore later with:\n  TARGET_DATABASE_URL=... %s --restore-only %s\n' "$0" "$DUMP_FILE"; exit 0; }
fi

# --- Restore ---------------------------------------------------------------

if [ "$MODE" = full ] || [ "$MODE" = restore ]; then
  step "Restoring into the target"
  existing="$(sql "$TGT" "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")"
  if [ "${existing:-0}" -gt 0 ]; then
    warn "target already holds $existing tables — they will be dropped and replaced (--clean)"
    if [ -t 0 ] && [ "${FORCE:-}" != "true" ]; then
      printf '    type the word replace to continue: '; read -r answer
      [ "$answer" = "replace" ] || die "aborted"
    fi
  fi
  # A managed database owns the public schema and its extensions itself, so
  # the restore must not try to drop or recreate either. Filter those entries
  # out of the table of contents; everything else — tables, sequences,
  # indexes, constraints, data — goes through unchanged.
  TOC="$DUMP_FILE.toc"
  pg_restore --list "$DUMP_FILE" \
    | grep -vE '^[0-9]+; [0-9]+ [0-9]+ (SCHEMA - public|EXTENSION |COMMENT - EXTENSION)' > "$TOC"
  skipped="$(pg_restore --list "$DUMP_FILE" | grep -cE '^[0-9]+; [0-9]+ [0-9]+ (SCHEMA - public|EXTENSION |COMMENT - EXTENSION)' || true)"
  [ "${skipped:-0}" -gt 0 ] && ok "skipping $skipped schema/extension entries the managed database owns"
  set +e
  pg_restore --dbname="$TGT" --use-list="$TOC" \
    --no-owner --no-privileges --clean --if-exists --exit-on-error \
    "$DUMP_FILE"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    die "pg_restore exited with $rc; nothing above this line is trustworthy — fix the cause and re-run with --restore-only $DUMP_FILE"
  fi
  ok "restored"
  # pg_dump output begins by emptying search_path for its session (and sets
  # half a dozen other session parameters). Behind a connection pooler that
  # does not reset session state between clients — Serverless SQL Database
  # documents exactly this — the backend the restore used keeps those
  # settings and is handed to the next clients, which then cannot see any
  # table by its bare name: the app answers 500, the verifier is told the
  # tables do not exist. Reset it. Each call is a fresh client connection, so
  # a handful of them reaches the pooled backend(s) involved.
  for _ in 1 2 3 4 5 6 7 8; do sql "$TGT" "reset all" >/dev/null 2>&1 || true; done
  ok "session settings the restore left on pooled connections reset"
  sql "$TGT" "analyze" >/dev/null 2>&1 && ok "statistics refreshed (ANALYZE)" || warn "ANALYZE was refused; harmless"
fi

# --- Verify ----------------------------------------------------------------

step "Verifying"
if [ -n "${DUMP_FILE:-}" ] && [ -f "$DUMP_FILE.counts" ]; then
  expected_counts="$(cat "$DUMP_FILE.counts")"; expected_media="$(cat "$DUMP_FILE.media")"
  yardstick="the dump"
elif [ -n "$SRC" ]; then
  expected_counts="$(row_counts "$SRC")"; expected_media="$(media_bytes "$SRC")"
  yardstick="the source, live"
else
  die "nothing to verify against: give SOURCE_DATABASE_URL or a dump with its .counts file"
fi
# A pooled managed database can hand the next connection a backend whose
# catalog view predates the DDL the restore just ran ("relation does not
# exist" for a table that is plainly there). It clears within a minute or two;
# judge the copy on a connection that sees the tables, not on the first one.
actual_counts=""
for attempt in $(seq 1 15); do
  if actual_counts="$(row_counts "$TGT" 2>/dev/null)" && [ -n "$actual_counts" ]; then break; fi
  warn "target not yet consistent (attempt $attempt/15); waiting 10s"
  actual_counts=""; sleep 10
done
[ -n "$actual_counts" ] || die "could not read the target's tables after 150s; re-run with --verify-only"
actual_media="$(media_bytes "$TGT")"

status=0
if diff <(printf '%s\n' "$expected_counts") <(printf '%s\n' "$actual_counts") > /dev/null; then
  ok "row counts match $yardstick for every table ($(printf '%s\n' "$actual_counts" | wc -l | tr -d ' ') tables, $(printf '%s\n' "$actual_counts" | awk -F'\t' '{s+=$2} END {print s+0}') rows)"
else
  status=1
  printf '    %s✗%s row counts differ (expected from %s vs target):\n' "$R" "$N" "$yardstick"
  diff --side-by-side --suppress-common-lines <(printf '%s\n' "$expected_counts") <(printf '%s\n' "$actual_counts") | sed 's/^/      /' || true
fi
if [ "$expected_media" = "$actual_media" ]; then
  ok "uploaded images: $actual_media bytes on both sides"
else
  status=1; printf '    %s✗%s uploaded images: %s bytes expected, %s on the target\n' "$R" "$N" "$expected_media" "$actual_media"
fi
# The migration ledger decides what `manage.py migrate` will do next; it must
# have come across intact or the first deploy re-runs history.
m="$(sql "$TGT" "select count(*) from public.django_migrations")"
[ "${m:-0}" -gt 0 ] && ok "django_migrations has $m rows" || { status=1; printf '    %s✗%s django_migrations is empty\n' "$R" "$N"; }

if [ $status -eq 0 ]; then
  printf '\n%sThe target is a complete copy.%s\n' "$G$B" "$N"
else
  printf '\n%sThe target is NOT a complete copy.%s Do not point traffic at it.\n' "$R$B" "$N"
fi
exit $status
