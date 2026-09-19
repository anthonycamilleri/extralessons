#!/usr/bin/env bash
#
# Print the full `environment-variables.KEY=VALUE` argument list a Scaleway
# resource needs after merging some variables into what it already has.
#
#   deploy/scw-merge-env.sh container <container-id> KEY=VALUE... [--unset KEY]...
#   deploy/scw-merge-env.sh job <job-definition-id> KEY=VALUE... [--unset KEY]...
#
# `scw container container update` and `scw jobs definition update` replace the
# plain environment map wholesale, so setting one variable means re-sending
# all of them. This reads the current map, applies the given changes, and
# prints one argument per line for `mapfile -t ARGS < <(...)`. Nothing is
# written: the caller passes ARGS to the update it was going to make anyway
# (image change and variables in one call means one redeploy, not two).
#
# Only the plain map is handled. A container's secrets merge on their own
# (keys not mentioned are kept), so they need no help; jobs have no secrets.
#
# Values must not contain newlines. Keys are printed sorted, so two runs with
# the same inputs produce the same list.

set -euo pipefail

kind="${1:?usage: scw-merge-env.sh container|job <id> KEY=VALUE... [--unset KEY]...}"
id="${2:?missing resource id}"
shift 2

case "$kind" in
  container) current="$(scw container container get "$id" -o json)" ;;
  job)       current="$(scw jobs definition get "$id" -o json)" ;;
  *) echo "scw-merge-env.sh: kind must be 'container' or 'job', not '$kind'" >&2; exit 2 ;;
esac

# Build the change set as JSON so values with '=' or spaces survive intact.
set_json='{}'; unset_json='[]'
while [ $# -gt 0 ]; do
  if [ "$1" = "--unset" ]; then
    unset_json="$(jq -c --arg k "${2:?--unset needs a key}" '. + [$k]' <<<"$unset_json")"; shift 2
  else
    case "$1" in
      *=*) ;;
      *) echo "scw-merge-env.sh: expected KEY=VALUE, got '$1'" >&2; exit 2 ;;
    esac
    set_json="$(jq -c --arg k "${1%%=*}" --arg v "${1#*=}" '. + {($k): $v}' <<<"$set_json")"; shift
  fi
done

jq -r --argjson set "$set_json" --argjson unset "$unset_json" '
  ((.environment_variables // {}) + $set)
  | with_entries(select(.key as $k | $unset | index($k) | not))
  | to_entries | sort_by(.key)[]
  | "environment-variables.\(.key)=\(.value)"
' <<<"$current"
