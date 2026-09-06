#!/usr/bin/env bash
#
# Start a Scaleway Serverless Job and wait for it, failing if it did not
# succeed. Used by the deploy workflow (migrations before the container rolls)
# and by the migration workflow (migrations after the restore).
#
#   deploy/scw-run-job.sh <job-definition-id> [args.0=... args.1=...]
#
# Extra arguments are passed to `scw jobs definition start` as contextual
# overrides for that one run — `args.0=ensure_admin`, say — and leave the
# definition untouched.

set -euo pipefail

JOB_ID="${1:?usage: scw-run-job.sh <job-definition-id> [start args...]}"
shift

# The response is {"job_runs":[{...}]}, not a bare run object.
run_id="$(scw jobs definition start "$JOB_ID" "$@" -o json | jq -r '.job_runs[0].id // .id')"
[ -n "$run_id" ] && [ "$run_id" != "null" ] || { echo "could not start job $JOB_ID" >&2; exit 1; }
echo "job run $run_id started"

# `wait` blocks until a stable state but is not the authority on which one.
scw jobs run wait "$run_id" >/dev/null 2>&1 || true
run_json="$(scw jobs run get "$run_id" -o json)"
state="$(jq -r '.state // "unknown"' <<<"$run_json")"
echo "job run $run_id finished: $state"
if [ "$state" != "succeeded" ]; then
  jq . <<<"$run_json" >&2
  echo "inspect with: scw jobs run get $run_id (logs are in Cockpit)" >&2
  exit 1
fi
