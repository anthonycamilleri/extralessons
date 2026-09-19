"""robots.txt keeps crawlers to the public pages and costs nothing to serve."""
import pytest
from django.urls import reverse

from config.robots import DISALLOWED


def _rules(response):
    return [line for line in response.content.decode().splitlines() if line]


def test_robots_is_plain_text_and_cached(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/plain")
    assert "max-age=86400" in response.headers["Cache-Control"]
    assert reverse("robots_txt") == "/robots.txt"


def test_robots_allows_the_catalogue_and_blocks_the_rest(client):
    rules = _rules(client.get("/robots.txt"))

    assert rules[0] == "User-agent: *"
    assert rules[-1] == "Allow: /"
    for path in ("/admin/", "/accounts/", "/me/", "/provider/", "/mcp", "/media/"):
        assert f"Disallow: {path}" in rules
    # Every disallowed prefix is a real route, so a rename here does not leave
    # the file pointing at nothing.
    assert "Disallow: /_health" in rules
    assert len([r for r in rules if r.startswith("Disallow: ")]) == len(DISALLOWED)


@pytest.mark.django_db
def test_robots_does_not_touch_the_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/robots.txt")

    assert response.status_code == 200


def test_robots_only_answers_get(client):
    assert client.post("/robots.txt").status_code == 405
