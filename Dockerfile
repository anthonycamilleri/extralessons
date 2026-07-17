FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir pytest-django factory-boy debugpy

COPY . .

RUN DJANGO_SETTINGS_MODULE=config.settings.dev SECRET_KEY=build python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--workers", "3", "--bind", "0.0.0.0:8000"]
