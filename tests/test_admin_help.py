"""The in-app help: who can read it, that every page renders, and that the
screenshots a guide asks for actually exist."""
import re

import pytest
from django.urls import reverse

from apps.dashboards.help import registry

from .factories import AdminFactory, ProviderUserFactory, SuperAdminFactory, UserFactory

pytestmark = pytest.mark.django_db

ADMIN = registry.AUDIENCES["admin"]
SCREENSHOTS = "static/img/help"


def slugs():
    return [topic.slug for topic in ADMIN.topics]


class TestWhoCanRead:
    @pytest.mark.parametrize("factory", [AdminFactory, SuperAdminFactory])
    def test_admins_can_read_every_page(self, client, factory):
        client.force_login(factory())
        assert client.get(reverse("admin:help_index")).status_code == 200
        for slug in slugs():
            response = client.get(reverse("admin:help_topic", args=[slug]))
            assert response.status_code == 200, slug

    @pytest.mark.parametrize("factory", [UserFactory, ProviderUserFactory])
    def test_everyone_else_is_sent_to_the_login_page(self, client, factory):
        client.force_login(factory())
        response = client.get(reverse("admin:help_index"))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_a_visitor_is_sent_to_the_login_page(self, client):
        response = client.get(reverse("admin:help_index"))
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]


class TestThePages:
    def test_the_index_lists_every_topic(self, client):
        client.force_login(AdminFactory())
        page = client.get(reverse("admin:help_index")).content.decode()
        for topic in ADMIN.topics:
            assert topic.title in page
            assert reverse("admin:help_topic", args=[topic.slug]) in page

    def test_an_unknown_page_is_a_404(self, client):
        client.force_login(AdminFactory())
        assert client.get("/admin/help/how-to-fly/").status_code == 404

    def test_a_page_renders_its_markdown(self, client):
        client.force_login(AdminFactory())
        page = client.get(reverse("admin:help_topic", args=["approving-requests"])).content.decode()
        assert "<h1" in page and "Approving requests" in page
        # attr_list gives the headings the stable anchors contextual links use.
        assert 'id="rule"' in page

    def test_links_between_guides_resolve_to_help_urls(self, client):
        client.force_login(AdminFactory())
        page = client.get(reverse("admin:help_topic", args=["approving-requests"])).content.decode()
        assert f'href="{reverse("admin:help_topic", args=["waiting-lists"])}"' in page

    def test_a_missing_screenshot_leaves_the_caption_behind(self, client, monkeypatch):
        """Manifest storage raises on an unknown name; the page must survive."""
        from apps.dashboards.help import markdown_ext

        class NothingCollected:
            def url(self, name):
                raise ValueError(name)

        # Only the name the extension reaches for: the page's own CSS and JS
        # still have to resolve.
        monkeypatch.setattr(markdown_ext, "staticfiles_storage", NothingCollected())
        client.force_login(AdminFactory())
        response = client.get(reverse("admin:help_topic", args=["approving-requests"]))
        assert response.status_code == 200
        assert b"<img" not in response.content


class TestTheHelpIsReachable:
    def test_every_admin_page_carries_a_help_link(self, client):
        client.force_login(AdminFactory())
        page = client.get(reverse("admin:index")).content.decode()
        assert reverse("admin:help_index") in page

    @pytest.mark.parametrize(
        "url_name,slug",
        [
            ("admin:enrollments_enrollment_requests", "approving-requests"),
            ("admin:notifications_broadcast_add", "announcements"),
        ],
    )
    def test_the_busy_pages_point_at_the_right_guide(self, client, url_name, slug):
        client.force_login(SuperAdminFactory())
        page = client.get(reverse(url_name)).content.decode()
        assert reverse("admin:help_topic", args=[slug]) in page


class TestTheContent:
    """Guards the two ways a guide silently rots: a renamed file, a renamed
    screenshot."""

    def test_every_registered_topic_has_a_file(self):
        for topic in ADMIN.topics:
            assert ADMIN.path(topic).is_file(), topic.slug

    def test_every_file_is_registered(self):
        on_disk = {path.stem for path in (registry.CONTENT_ROOT / ADMIN.key).glob("*.md")}
        assert on_disk == set(slugs())

    def test_every_screenshot_a_guide_asks_for_exists(self, settings):
        images = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
        missing = []
        for topic in ADMIN.topics:
            for name in images.findall(ADMIN.path(topic).read_text(encoding="utf-8")):
                if not (settings.BASE_DIR / SCREENSHOTS / ADMIN.key / name).is_file():
                    missing.append(f"{topic.slug}: {name}")
        assert not missing, f"help screenshots missing: {missing}"

    def test_every_link_between_guides_points_somewhere(self):
        links = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
        broken = []
        for topic in ADMIN.topics:
            for href in links.findall(ADMIN.path(topic).read_text(encoding="utf-8")):
                if href.startswith(("http", "/", "#", "mailto:")):
                    continue
                if href.partition("#")[0] not in slugs():
                    broken.append(f"{topic.slug}: {href}")
        assert not broken, f"help links to nowhere: {broken}"
