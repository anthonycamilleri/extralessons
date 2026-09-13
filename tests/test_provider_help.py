"""The provider guides: who can read them, that every page renders, and that
the files, screenshots and cross-links they rely on exist."""
import re

import pytest
from django.urls import reverse

from apps.dashboards.help import registry

from .factories import AdminFactory, ProviderUserFactory, UserFactory

pytestmark = pytest.mark.django_db

PROVIDER = registry.AUDIENCES["provider"]
SCREENSHOTS = "static/img/help"


def slugs():
    return [topic.slug for topic in PROVIDER.topics]


class TestWhoCanRead:
    def test_providers_can_read_every_page(self, client):
        client.force_login(ProviderUserFactory())
        assert client.get(reverse("provider_help_index")).status_code == 200
        for slug in slugs():
            assert client.get(reverse("provider_help_topic", args=[slug])).status_code == 200, slug

    @pytest.mark.parametrize("factory", [UserFactory, AdminFactory])
    def test_other_roles_are_refused(self, client, factory):
        client.force_login(factory())
        assert client.get(reverse("provider_help_index")).status_code == 403

    def test_a_visitor_is_sent_to_the_login_page(self, client):
        response = client.get(reverse("provider_help_index"))
        assert response.status_code == 302
        assert reverse("login") in response["Location"]


class TestThePages:
    def test_the_index_lists_every_topic(self, client):
        client.force_login(ProviderUserFactory())
        page = client.get(reverse("provider_help_index")).content.decode()
        for topic in PROVIDER.topics:
            assert topic.title in page
            assert reverse("provider_help_topic", args=[topic.slug]) in page

    def test_an_unknown_page_is_a_404(self, client):
        client.force_login(ProviderUserFactory())
        assert client.get("/provider/help/how-to-fly/").status_code == 404

    def test_a_page_renders_its_markdown_on_the_public_layout(self, client):
        client.force_login(ProviderUserFactory())
        page = client.get(reverse("provider_help_topic", args=["attendance"])).content.decode()
        assert "<h1" in page and "Taking attendance" in page
        assert 'id="edit"' in page
        assert "My classes" in page  # the site navigation, not the admin's

    def test_links_between_guides_resolve_to_provider_help_urls(self, client):
        client.force_login(ProviderUserFactory())
        page = client.get(
            reverse("provider_help_topic", args=["getting-started"])
        ).content.decode()
        assert f'href="{reverse("provider_help_topic", args=["your-classes"])}"' in page
        assert "/admin/help/" not in page

    def test_the_admin_guides_still_link_to_admin_urls(self, client):
        client.force_login(AdminFactory())
        page = client.get(reverse("admin:help_topic", args=["approving-requests"])).content.decode()
        assert f'href="{reverse("admin:help_topic", args=["waiting-lists"])}"' in page


class TestTheHelpIsReachable:
    @pytest.mark.parametrize(
        "url_name,slug",
        [
            ("provider_home", None),
            ("provider_broadcast", "messaging-families"),
            ("provider_instructors", "managing-instructors"),
        ],
    )
    def test_the_busy_pages_point_at_the_right_guide(self, client, url_name, slug):
        from apps.catalog.models import Provider

        user = ProviderUserFactory()
        Provider.objects.create(name="P").members.add(user)
        client.force_login(user)
        page = client.get(reverse(url_name)).content.decode()
        assert reverse("provider_help_index") in page
        if slug:
            assert reverse("provider_help_topic", args=[slug]) in page


class TestTheContent:
    def test_every_registered_topic_has_a_file(self):
        for topic in PROVIDER.topics:
            assert PROVIDER.path(topic).is_file(), topic.slug

    def test_every_file_is_registered(self):
        on_disk = {path.stem for path in (registry.CONTENT_ROOT / PROVIDER.key).glob("*.md")}
        assert on_disk == set(slugs())

    def test_every_screenshot_a_guide_asks_for_exists(self, settings):
        images = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
        missing = []
        for topic in PROVIDER.topics:
            for name in images.findall(PROVIDER.path(topic).read_text(encoding="utf-8")):
                if not (settings.BASE_DIR / SCREENSHOTS / PROVIDER.key / name).is_file():
                    missing.append(f"{topic.slug}: {name}")
        assert not missing, f"help screenshots missing: {missing}"

    def test_every_link_between_guides_points_somewhere(self):
        links = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
        broken = []
        for topic in PROVIDER.topics:
            for href in links.findall(PROVIDER.path(topic).read_text(encoding="utf-8")):
                if href.startswith(("http", "/", "#", "mailto:")):
                    continue
                if href.partition("#")[0] not in slugs():
                    broken.append(f"{topic.slug}: {href}")
        assert not broken, f"help links to nowhere: {broken}"
