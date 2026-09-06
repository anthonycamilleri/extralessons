"""The bare domain is redirected to the canonical hostname, permanently, with
the path and query intact, and nothing else is touched.
"""
import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def canonical(settings):
    settings.ALLOWED_HOSTS = ["www.esljparents.eu", "esljparents.eu", "gen.functions.fnc.fr-par.scw.cloud"]
    settings.SITE_URL = "https://www.esljparents.eu"
    settings.CANONICAL_REDIRECT_HOSTS = ["esljparents.eu"]
    settings.SECURE_SSL_REDIRECT = False


@pytest.mark.django_db
def test_apex_is_redirected_with_path_and_query(client, canonical, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/classes/?day=2&age=7", headers={"host": "esljparents.eu"})

    assert response.status_code == 301
    assert response["Location"] == "https://www.esljparents.eu/classes/?day=2&age=7"


def test_apex_with_port_and_capitals_is_redirected(client, canonical):
    response = client.get("/", headers={"host": "ESLJParents.eu:443"})

    assert response.status_code == 301
    assert response["Location"] == "https://www.esljparents.eu/"


@pytest.mark.django_db
def test_canonical_and_generated_hosts_are_served(client, canonical):
    assert client.get("/", headers={"host": "www.esljparents.eu"}).status_code == 200
    assert client.get("/", headers={"host": "gen.functions.fnc.fr-par.scw.cloud"}).status_code == 200


def test_health_probe_is_never_redirected(client, canonical):
    response = client.get("/_health", headers={"host": "esljparents.eu"})

    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_off_when_unconfigured(client, settings):
    settings.ALLOWED_HOSTS = ["esljparents.eu"]
    settings.CANONICAL_REDIRECT_HOSTS = []

    assert client.get("/", headers={"host": "esljparents.eu"}).status_code == 200
