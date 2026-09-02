"""Logged-in users change their password without typing the current one."""
import pytest
from django.urls import reverse

from .factories import AdminFactory, ProviderUserFactory, UserFactory

pytestmark = pytest.mark.django_db

NEW = "a-brand-new-passw0rd"


def test_parent_changes_password_without_the_old_one(client):
    parent = UserFactory(password="old-pass-123")
    client.force_login(parent)

    page = client.get(reverse("password_change")).content.decode()
    assert 'name="new_password1"' in page
    assert 'name="old_password"' not in page

    response = client.post(
        reverse("password_change"), {"new_password1": NEW, "new_password2": NEW}
    )
    assert response.status_code == 302
    parent.refresh_from_db()
    assert parent.check_password(NEW)
    # The session survives the change (Django rotates the session hash).
    assert client.get(reverse("parent_home")).status_code == 200


def test_admin_change_password_link_uses_the_same_form(client):
    admin = AdminFactory()
    client.force_login(admin)
    url = reverse("admin:password_change")
    assert url == "/admin/password_change/"
    page = client.get(url).content.decode()
    assert 'name="old_password"' not in page
    client.post(url, {"new_password1": NEW, "new_password2": NEW})
    admin.refresh_from_db()
    assert admin.check_password(NEW)


def test_mismatch_is_rejected(client):
    user = ProviderUserFactory(password="old-pass-123")
    client.force_login(user)
    response = client.post(
        reverse("password_change"), {"new_password1": NEW, "new_password2": "different"}
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("old-pass-123")


def test_anonymous_is_sent_to_login(client):
    response = client.get(reverse("password_change"))
    assert response.status_code == 302
    assert reverse("login") in response.url
