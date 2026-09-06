#!/bin/sh
#
# The two things that run against the database before a new image takes
# traffic: migrations, then the first-admin bootstrap.
#
# On Scaleway the deploy workflow runs them as two runs of the migrate job
# (deploy.yml), so nothing there calls this file; it exists for platforms with
# a single pre-deploy command (Render: render.yaml -> preDeployCommand), which
# run it without a shell — hence a script rather than "a && b". CI runs it
# inside the built image (see ci.yml), so it stays proven either way.
#
# Ordering assumes additive migrations, the normal Django case. If any step
# fails the deploy is abandoned and the previous version keeps serving.

set -eu

python manage.py migrate --noinput
# First deploy: creates the ADMIN_EMAIL account and emails a set-password link.
# Every later deploy: finds it and does nothing.
python manage.py ensure_admin
