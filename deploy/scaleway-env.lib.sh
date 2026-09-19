# The production environment of the Scaleway estate, in one place.
#
# Sourced by deploy/provision.sh (fresh estate) and by
# .github/workflows/scaleway-configure.yml (existing estate), so the web
# container and the two jobs can never disagree about a variable, and a new
# setting is added here once.
#
# Each function fills a bash array with `scw` arguments from the variables
# named in its comment. Callers export those variables first.
#
# The one thing to know about `scw container container update`: BOTH
# `environment-variables.*` and `secret-environment-variables.*` replace their
# map wholesale. Naming one secret deletes every other secret on the container
# (this took production down once: a token rotation left the container with
# that token only, so SECRET_KEY and DATABASE_URL vanished and the app came up
# on an empty SQLite file). Every update must therefore pass the complete
# plain set AND the complete secret set, which is what these functions emit.
# Secrets cannot be read back from the container; SECRET_KEY and DATABASE_URL
# are recovered from the migrate job's plain variables, the rest come from
# GitHub secrets (deploy.yml shows the pattern).
#
# Jobs take plain variables only, so their secrets are plain too. Anyone who
# can read a job definition can read them; docs/scaleway-setup.md describes
# the Secret Manager route for tightening that.
#
# Email goes out through Scaleway Transactional Email over SMTP (scw_email_env):
# smtp.tem.scaleway.com, port 587 with STARTTLS, username = the project id the
# sending domain is registered in, password = an API secret key of an IAM
# principal allowed to send (TransactionalEmailFullAccess or EmailSend). The
# deploy workflow applies exactly these variables on every deploy, merged into
# whatever else the resources carry (deploy/scw-merge-env.sh), so the mail
# set-up cannot drift from this file.

# The email service, as bare KEY=VALUE pairs so the same list can be applied
# as a full set (provision.sh, the configure workflow) or merged into an
# existing resource (deploy.yml via deploy/scw-merge-env.sh).
# Needs: EMAIL_HOST_USER (the project id) EMAIL_HOST_PASSWORD (the secret key)
# Optional, with defaults: EMAIL_HOST EMAIL_PORT EMAIL_USE_TLS EMAIL_BACKEND
scw_email_env() { # fills EMAIL_VARS (plain) and EMAIL_SECRET_VARS (the password)
  EMAIL_VARS=(
    # Explicit, so a ZeptoMail token still present on a resource cannot win
    # (config/settings/prod.py picks ZeptoMail when the token is set and
    # EMAIL_BACKEND is not).
    EMAIL_BACKEND="${EMAIL_BACKEND:-django.core.mail.backends.smtp.EmailBackend}"
    EMAIL_HOST="${EMAIL_HOST:-smtp.tem.scaleway.com}"
    # 587 = STARTTLS. Serverless blocks outbound 25 and 465 to anything but
    # Scaleway's own mail servers; 587 is open everywhere and is what Django's
    # EMAIL_USE_TLS means.
    EMAIL_PORT="${EMAIL_PORT:-587}"
    EMAIL_USE_TLS="${EMAIL_USE_TLS:-true}"
    EMAIL_HOST_USER="${EMAIL_HOST_USER:?EMAIL_HOST_USER (the project id) is required}"
  )
  EMAIL_SECRET_VARS=(
    EMAIL_HOST_PASSWORD="${EMAIL_HOST_PASSWORD:?EMAIL_HOST_PASSWORD (the secret key) is required}"
  )
}

# Plain (non-secret) settings shared by the container and both jobs.
# Needs: TIME_ZONE ALLOWED_HOSTS CSRF_TRUSTED_ORIGINS SITE_URL
#        DEFAULT_FROM_EMAIL ADMIN_EMAIL; APEX_DOMAIN may be empty;
#        everything scw_email_env needs
# Optional, with defaults: LOG_LEVEL DB_POOL_MAX_SIZE NOTIFIER_DRAIN_MAX_SECONDS
#        WHATSAPP_ENABLED ZEPTOMAIL_API_URL
scw_plain_env() { # fills PLAIN_ENV
  scw_email_env
  local kv
  PLAIN_ENV=(
    environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod
    environment-variables.TIME_ZONE="${TIME_ZONE:-Europe/Malta}"
    environment-variables.LOG_LEVEL="${LOG_LEVEL:-INFO}"
    environment-variables.ALLOWED_HOSTS="$ALLOWED_HOSTS"
    environment-variables.CSRF_TRUSTED_ORIGINS="$CSRF_TRUSTED_ORIGINS"
    environment-variables.SITE_URL="$SITE_URL"
    # The bare domain redirects to SITE_URL (config/canonical.py). Empty when
    # there is no apex, which the app reads as "no redirects".
    environment-variables.CANONICAL_REDIRECT_HOSTS="${APEX_DOMAIN:-}"
    environment-variables.DEFAULT_FROM_EMAIL="$DEFAULT_FROM_EMAIL"
    # Kept for a return to ZeptoMail (docs/scaleway-setup.md, "Returning to ZeptoMail"); inert
    # while EMAIL_BACKEND names SMTP.
    environment-variables.ZEPTOMAIL_API_URL="${ZEPTOMAIL_API_URL:-https://api.zeptomail.eu/v1.1/email}"
    environment-variables.ADMIN_EMAIL="${ADMIN_EMAIL:-}"
    # Matches gunicorn's eight threads (deploy/gunicorn.conf.py); raise it and
    # max-scale together, within the database's connection ceiling.
    environment-variables.DB_POOL_MAX_SIZE="${DB_POOL_MAX_SIZE:-8}"
    # Time budget for the nightly drain; must exceed the job's --max-seconds.
    environment-variables.NOTIFIER_DRAIN_MAX_SECONDS="${NOTIFIER_DRAIN_MAX_SECONDS:-300}"
    environment-variables.WHATSAPP_ENABLED="${WHATSAPP_ENABLED:-false}"
  )
  for kv in "${EMAIL_VARS[@]}"; do PLAIN_ENV+=("environment-variables.$kv"); done
}

# Secrets for the web container (stored encrypted by the platform). This is
# the COMPLETE secret set: an update carrying it replaces whatever the
# container had, so anything not listed here is gone after the call.
# Needs: SECRET_KEY DATABASE_URL, and scw_email_env's
# Optional: MCP_API_TOKEN (empty switches the /mcp endpoint off),
#           ZEPTOMAIL_SEND_MAIL_TOKEN (only sent when set)
scw_container_secrets() { # fills CONTAINER_SECRETS
  : "${SECRET_KEY:?SECRET_KEY is required (read it from the migrate job)}"
  : "${DATABASE_URL:?DATABASE_URL is required (read it from the migrate job)}"
  scw_email_env
  local kv
  CONTAINER_SECRETS=(
    secret-environment-variables.SECRET_KEY="$SECRET_KEY"
    secret-environment-variables.DATABASE_URL="$DATABASE_URL"
  )
  for kv in "${EMAIL_SECRET_VARS[@]}"; do CONTAINER_SECRETS+=("secret-environment-variables.$kv"); done
  # Absent and empty mean the same to the app (endpoint off); only send a value.
  [ -n "${MCP_API_TOKEN:-}" ] && \
    CONTAINER_SECRETS+=(secret-environment-variables.MCP_API_TOKEN="$MCP_API_TOKEN")
  [ -n "${ZEPTOMAIL_SEND_MAIL_TOKEN:-}" ] && \
    CONTAINER_SECRETS+=(secret-environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="$ZEPTOMAIL_SEND_MAIL_TOKEN")
  return 0
}

# The complete environment of a job: the shared plain set plus the secrets
# the migrate step (SECRET_KEY, DATABASE_URL, and the mail password for
# ensure_admin and send_test_email) and the notifier (all three, to send) need.
# Needs: everything scw_plain_env needs, plus SECRET_KEY DATABASE_URL
# Optional: ZEPTOMAIL_SEND_MAIL_TOKEN
scw_job_env() { # fills JOB_ENV
  scw_plain_env
  local kv
  JOB_ENV=(
    "${PLAIN_ENV[@]}"
    environment-variables.SECRET_KEY="$SECRET_KEY"
    environment-variables.DATABASE_URL="$DATABASE_URL"
  )
  for kv in "${EMAIL_SECRET_VARS[@]}"; do JOB_ENV+=("environment-variables.$kv"); done
  [ -n "${ZEPTOMAIL_SEND_MAIL_TOKEN:-}" ] && \
    JOB_ENV+=(environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="$ZEPTOMAIL_SEND_MAIL_TOKEN")
  return 0
}

# Hostnames Django must accept and origins it must trust, from the public
# domain (optionally with its apex) and the endpoint Scaleway generated.
# Needs: DOMAIN (may be empty) GENERATED_HOST; optional APEX_DOMAIN
scw_host_settings() { # exports ALLOWED_HOSTS CSRF_TRUSTED_ORIGINS SITE_URL WEB_HOST
  local hosts="" origins=""
  if [ -n "${DOMAIN:-}" ]; then
    hosts="$DOMAIN"; origins="https://$DOMAIN"
    if [ -n "${APEX_DOMAIN:-}" ]; then hosts="$hosts,$APEX_DOMAIN"; origins="$origins,https://$APEX_DOMAIN"; fi
    WEB_HOST="$DOMAIN"
  else
    WEB_HOST="$GENERATED_HOST"
  fi
  # The platform routes health probes and direct traffic over the generated
  # endpoint even once a domain is attached, so it is always accepted.
  case ",$hosts," in *",$GENERATED_HOST,"*) ;; *) hosts="${hosts:+$hosts,}$GENERATED_HOST" ;; esac
  origins="${origins:+$origins,}https://$GENERATED_HOST"
  export ALLOWED_HOSTS="$hosts" CSRF_TRUSTED_ORIGINS="$origins" SITE_URL="https://$WEB_HOST" WEB_HOST
}
