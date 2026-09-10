"""The "send me a test email" button: who may press it, what it sends, and
that it leaves no trace."""
from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse

from apps.notifications.channels.base import ChannelError
from apps.notifications.models import Broadcast, Notification, NotificationTemplate

from .factories import AdminFactory, ProviderUserFactory, SuperAdminFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = reverse("announcement_test_send")
DRAFT = {"subject": "Friday trip", "body_html": "<p>Bring <strong>boots</strong>.</p>"}


class TestWhoMayPressIt:
    def test_anonymous_is_sent_to_login(self, client):
        response = client.post(URL, DRAFT)
        assert response.status_code == 302
        assert reverse("login") in response.url
        assert mail.outbox == []

    def test_parent_is_forbidden(self, client):
        client.force_login(UserFactory())
        assert client.post(URL, DRAFT).status_code == 403
        assert mail.outbox == []

    def test_get_is_not_allowed(self, client):
        client.force_login(SuperAdminFactory())
        assert client.get(URL).status_code == 405


class TestWhatItSends:
    @pytest.mark.parametrize("make_user", [AdminFactory, SuperAdminFactory, ProviderUserFactory])
    def test_goes_to_the_author_only(self, client, make_user):
        user = make_user(first_name="Sam", last_name="Office")
        client.force_login(user)

        response = client.post(URL, DRAFT)

        assert response.status_code == 200
        assert response.json() == {"sent_to": user.email}
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == [user.email]
        assert sent.subject == "[Test] Friday trip"
        assert "Hi Sam Office" in sent.body
        assert "Bring boots." in sent.body
        html, mimetype = sent.alternatives[0]
        assert mimetype == "text/html"
        assert "Bring <strong>boots</strong>." in html
        assert "Hi Sam Office" in html

    def test_nothing_is_recorded(self, client):
        client.force_login(SuperAdminFactory())
        client.post(URL, DRAFT)
        assert not Broadcast.objects.exists()
        assert not Notification.objects.exists()

    def test_the_draft_is_cleaned_like_a_real_send(self, client):
        client.force_login(SuperAdminFactory())
        client.post(URL, {"subject": "x", "body_html": '<p onclick="a()">Hi</p><script>b()</script>'})
        html = mail.outbox[0].alternatives[0][0]
        assert "<p style=\"margin:0 0 1em;\">Hi</p>" in html
        assert "script" not in html and "onclick" not in html


class TestWhatItRefuses:
    def test_blank_message(self, client):
        client.force_login(SuperAdminFactory())
        response = client.post(URL, {"subject": "x", "body_html": "<p><br></p>"})
        assert response.status_code == 400
        assert response.json() == {"error": "Write a message."}
        assert mail.outbox == []

    def test_missing_subject(self, client):
        client.force_login(SuperAdminFactory())
        response = client.post(URL, {"subject": "", "body_html": "<p>Hi</p>"})
        assert response.status_code == 400
        assert response.json() == {"error": "Give the message a subject first."}

    def test_disabled_template(self, client):
        NotificationTemplate.objects.filter(event="BROADCAST").update(enabled=False)
        client.force_login(SuperAdminFactory())
        response = client.post(URL, DRAFT)
        assert response.status_code == 400
        assert "template is disabled" in response.json()["error"]
        assert mail.outbox == []

    def test_delivery_failure_is_reported(self, client):
        client.force_login(SuperAdminFactory())
        adapter = mock.Mock()
        adapter.send.side_effect = ChannelError("Mailbox unavailable")
        with mock.patch("apps.notifications.channels.base.get_adapter", return_value=adapter):
            response = client.post(URL, DRAFT)
        assert response.status_code == 502
        assert response.json() == {"error": "The test could not be sent: Mailbox unavailable"}
