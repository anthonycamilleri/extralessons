"""MAINTENANCE_MODE freezes the site for a database move: everything but the
health probe answers 503, and nothing reaches the database while it does.
"""
import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
def test_off_by_default(client):
    assert client.get("/").status_code == 200


@pytest.mark.django_db
def test_every_page_is_frozen(client, settings, django_assert_num_queries):
    settings.MAINTENANCE_MODE = True

    with django_assert_num_queries(0):
        response = client.get("/")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "600"
    assert response.headers["Cache-Control"] == "no-store"
    assert b"Back shortly" in response.content


@pytest.mark.django_db
def test_writes_are_frozen_too(client, settings):
    settings.MAINTENANCE_MODE = True

    response = client.post("/accounts/login/", {"username": "x@example.com", "password": "y"})

    assert response.status_code == 503


def test_health_probe_still_answers(client, settings):
    """The platform must keep the instances up while the site is frozen."""
    settings.MAINTENANCE_MODE = True

    response = client.get("/_health")

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_message_can_be_customised(client, settings):
    settings.MAINTENANCE_MODE = True
    settings.MAINTENANCE_MESSAGE = "Moving house. Back at 07:00."

    response = client.get("/")

    assert b"Moving house. Back at 07:00." in response.content
