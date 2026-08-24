"""The platform's health probe has to answer under conditions that 400 or 301
every other URL: an unknown Host header, plain HTTP, and a cold database.
"""
import pytest
from django.test import Client


@pytest.fixture
def client():
    # enforce_csrf_checks is irrelevant here; a bare client keeps the probe
    # as close to what the platform sends as the test client allows.
    return Client()


def test_health_answers_for_an_unknown_host(client, settings):
    """Scaleway probes with its own Host header, which is not in ALLOWED_HOSTS."""
    settings.ALLOWED_HOSTS = ["activities.example.com"]

    response = client.get("/_health", headers={"host": "10.0.0.7"})

    assert response.status_code == 200
    assert response.content == b"ok"


def test_health_is_not_redirected_to_https(client, settings):
    """SecurityMiddleware would 301 this; the probe must be answered first."""
    settings.ALLOWED_HOSTS = ["*"]
    settings.SECURE_SSL_REDIRECT = True

    response = client.get("/_health")

    assert response.status_code == 200


@pytest.mark.django_db
def test_health_does_not_touch_the_database(client, django_assert_num_queries):
    """A probe that queried the database would fail instances during a database
    cold start — precisely when they are most needed."""
    with django_assert_num_queries(0):
        response = client.get("/_health")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_ordinary_urls_still_reject_an_unknown_host(client, settings):
    """Guard against the middleware swallowing host validation generally."""
    settings.ALLOWED_HOSTS = ["activities.example.com"]

    response = client.get("/", headers={"host": "10.0.0.7"})

    assert response.status_code == 400
