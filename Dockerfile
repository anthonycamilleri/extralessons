# syntax=docker/dockerfile:1
#
# One image, three roles. The web service runs it under gunicorn, the pre-deploy
# step runs the migrations from it, and the notifier cron job runs it with a
# different command. That is deliberate: three images would be three things to
# keep in step, and a migration that ran against code the web tier does not
# have is exactly the failure this avoids.
#
# Render builds this file with no --target, which yields the *last* stage, so
# `runtime` must stay last. The platform passes PORT in at run time (Render
# uses 10000); the ENV below is only the default for local runs and CI.
# Production is linux/amd64: build with `docker buildx build --platform
# linux/amd64` on an ARM machine when you need a faithful local image.

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app


FROM base AS builder
RUN python -m venv "$VIRTUAL_ENV"
COPY pyproject.toml ./
RUN pip install .


# Local development: dev tooling, root, source bind-mounted over /app by compose.
FROM builder AS dev
RUN pip install ".[dev]" debugpy
COPY . .
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# What ships. No test tooling, no debugger, non-root.
FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --from=builder /opt/venv /opt/venv
COPY . .

# collectstatic runs under the real production settings so a settings mistake
# breaks the build rather than the deploy. Bytecode is compiled here too:
# without it every cold start pays to re-compile the source tree.
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    SECRET_KEY=build-time-only-never-used-at-runtime \
    python manage.py collectstatic --noinput --clear \
 && python -m compileall -q /app \
 && chown -R app:app /app

USER app
ENV PORT=8080
EXPOSE 8080
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi"]
