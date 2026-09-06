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
# Two things to know about `scw container container update`:
#   * `environment-variables.*` replaces the plain map wholesale, so every
#     call must pass the complete set — which is what these functions emit;
#   * `secret-environment-variables.*` merges: keys not mentioned are kept.
#     Nothing here ever needs to delete a secret; S3_BUCKET (plain) is the
#     switch that decides whether the S3_* secrets are read at all.
#
# Jobs take plain variables only, so their secrets are plain too. Anyone who
# can read a job definition can read them; docs/scaleway-setup.md describes
# the Secret Manager route for tightening that.

# Plain (non-secret) settings shared by the container and both jobs.
# Needs: TIME_ZONE ALLOWED_HOSTS CSRF_TRUSTED_ORIGINS SITE_URL
#        DEFAULT_FROM_EMAIL ZEPTOMAIL_API_URL ADMIN_EMAIL
# Optional, with defaults: LOG_LEVEL DB_POOL_MAX_SIZE NOTIFIER_DRAIN_MAX_SECONDS WHATSAPP_ENABLED
scw_plain_env() { # fills PLAIN_ENV
  PLAIN_ENV=(
    environment-variables.DJANGO_SETTINGS_MODULE=config.settings.prod
    environment-variables.TIME_ZONE="${TIME_ZONE:-Europe/Malta}"
    environment-variables.LOG_LEVEL="${LOG_LEVEL:-INFO}"
    environment-variables.ALLOWED_HOSTS="$ALLOWED_HOSTS"
    environment-variables.CSRF_TRUSTED_ORIGINS="$CSRF_TRUSTED_ORIGINS"
    environment-variables.SITE_URL="$SITE_URL"
    environment-variables.DEFAULT_FROM_EMAIL="$DEFAULT_FROM_EMAIL"
    environment-variables.ZEPTOMAIL_API_URL="${ZEPTOMAIL_API_URL:-https://api.zeptomail.eu/v1.1/email}"
    environment-variables.ADMIN_EMAIL="${ADMIN_EMAIL:-}"
    # Matches gunicorn's eight threads (deploy/gunicorn.conf.py); raise it and
    # max-scale together, within the database's connection ceiling.
    environment-variables.DB_POOL_MAX_SIZE="${DB_POOL_MAX_SIZE:-8}"
    # Time budget for the nightly drain; must exceed the job's --max-seconds.
    environment-variables.NOTIFIER_DRAIN_MAX_SECONDS="${NOTIFIER_DRAIN_MAX_SECONDS:-300}"
    environment-variables.WHATSAPP_ENABLED="${WHATSAPP_ENABLED:-false}"
  )
}

# Secrets for the web container (stored encrypted by the platform).
# Needs: SECRET_KEY DATABASE_URL ZEPTOMAIL_SEND_MAIL_TOKEN MCP_API_TOKEN
scw_container_secrets() { # fills CONTAINER_SECRETS
  CONTAINER_SECRETS=(
    secret-environment-variables.SECRET_KEY="$SECRET_KEY"
    secret-environment-variables.DATABASE_URL="$DATABASE_URL"
    secret-environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="${ZEPTOMAIL_SEND_MAIL_TOKEN:-}"
    secret-environment-variables.MCP_API_TOKEN="${MCP_API_TOKEN:-}"
  )
}

# The complete environment of a job: the shared plain set plus the secrets
# the migrate step (SECRET_KEY, DATABASE_URL, and the email token for
# ensure_admin) and the notifier (all three, to send) need.
# Needs: everything scw_plain_env needs, plus SECRET_KEY DATABASE_URL ZEPTOMAIL_SEND_MAIL_TOKEN
scw_job_env() { # fills JOB_ENV
  scw_plain_env
  JOB_ENV=(
    "${PLAIN_ENV[@]}"
    environment-variables.SECRET_KEY="$SECRET_KEY"
    environment-variables.DATABASE_URL="$DATABASE_URL"
    environment-variables.ZEPTOMAIL_SEND_MAIL_TOKEN="${ZEPTOMAIL_SEND_MAIL_TOKEN:-}"
  )
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
