#!/bin/sh
#
# What Render runs between building the image and switching traffic to it:
#   render.yaml -> preDeployCommand: sh deploy/pre-deploy.sh
#
# A script rather than an inline command because Render does not pass the
# pre-deploy command through a shell: "a && b" arrives as arguments to "a".
# CI runs this same file inside the built image (see ci.yml), so a change here
# is proven before it can break a deploy.
#
# Ordering assumes additive migrations, the normal Django case. If any step
# fails the deploy is abandoned and the previous version keeps serving.

set -eu

python manage.py migrate --noinput
# First deploy: creates the ADMIN_EMAIL account and emails a set-password link.
# Every later deploy: finds it and does nothing.
python manage.py ensure_admin
