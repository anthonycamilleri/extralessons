#!/usr/bin/env bash
#
# Delete old tags of the application image from the Scaleway Container
# Registry, keeping the ones a rollback could still want.
#
#   deploy/scw-prune-images.sh <image-name> <tag-in-use> [keep]
#
# Every deploy pushes one tag per commit and nothing ever removed them, so the
# registry grew by one image per push. This keeps `latest`, the tag the
# container is running right now (whatever its age: a rollback deploy must not
# delete the image it just rolled to), and the `keep` newest tags besides
# (default 10 — ten deploys of rollback headroom), and deletes the rest.
# Registry storage is billed on the unique layers that remain, so freeing
# unreferenced manifests is what saves the money.
#
# Deleting a tag never touches the running container: Serverless pulled the
# image when it deployed and does not go back to the registry until the
# reference changes. Nothing is deleted when the listing cannot be read;
# nothing is deleted when fewer tags exist than are to be kept.

set -euo pipefail

image_name="${1:?usage: scw-prune-images.sh <image-name> <tag-in-use> [keep]}"
in_use="${2:?missing the tag currently deployed}"
keep="${3:-10}"

image_id="$(scw registry image list name="$image_name" -o json \
  | jq -r --arg n "$image_name" '.[]? | select(.name==$n) | .id' | head -1)"
if [ -z "$image_id" ]; then
  echo "prune: no image named '$image_name' in the registry; nothing to do"
  exit 0
fi

# Newest first. The first `keep` tags survive, as do `latest` and the tag in
# use wherever they fall in the list.
tags="$(scw registry tag list image-id="$image_id" order-by=created_at_desc -o json)"
total="$(jq 'length' <<<"$tags")"
doomed="$(jq -r --argjson keep "$keep" --arg use "$in_use" '
  [ .[] | select(.name != "latest" and .name != $use) ]
  | .[$keep:][]
  | "\(.id) \(.name) \(.created_at)"
' <<<"$tags")"

if [ -z "$doomed" ]; then
  echo "prune: $total tag(s) on $image_name, nothing older than the $keep kept; nothing deleted"
  exit 0
fi

deleted=0; failed=0
while read -r id name created; do
  [ -n "$id" ] || continue
  # Two tags of one digest (a SHA tag and `latest`, typically) refuse to be
  # deleted singly without force.
  if scw registry tag delete "$id" force=true -o json > /dev/null 2>&1; then
    echo "  deleted $name ($created)"; deleted=$((deleted+1))
  else
    echo "  could not delete $name ($id); leaving it"; failed=$((failed+1))
  fi
done <<<"$doomed"

echo "prune: $total tag(s) on $image_name, kept latest + $in_use + $keep newest; deleted $deleted, failed $failed"
# A failed delete is not worth failing a deploy that already serves traffic;
# the next deploy will try again.
exit 0
