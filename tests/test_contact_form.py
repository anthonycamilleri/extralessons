"""The public contact form: what it sends, and what it refuses to send."""
import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import SiteConfig
from apps.catalog import views as catalog_views
from apps.notifications.channels.email import EmailAdapter
from apps.notifications.models import Event, Notification

from .factories import UserFactory

pytestmark = pytest.mark.django_db

VALID = {
    "name": "Marta Novak",
    "email": "marta@example.com",
    "subject": "Judo waiting list",
    "message": "Is there any chance of a place in judo this term?",
}


@pytest.fixture(autouse=True)
def clear_throttle():
    """The flood guard lives in the cache, which outlives a test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def contact_address():
    config = SiteConfig.get()
    config.contact_email = "info@esljparents.eu"
    config.save()
    return config.contact_email


def contact_messages():
    return Notification.objects.filter(event=Event.CONTACT_MESSAGE)


class TestContactForm:
    def test_page_shows_the_form_and_the_address(self, client, contact_address):
        response = client.get(reverse("contact"))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'name="message"' in content
        assert contact_address in content

    def test_message_is_queued_to_the_contact_address(self, client, contact_address):
        response = client.post(reverse("contact"), VALID)

        assert response.status_code == 302
        row = contact_messages().get()
        assert row.recipient_email == contact_address
        assert row.recipient is None
        # The writer's address is the Reply-To, never the From.
        assert row.reply_to == "marta@example.com"
        assert row.status == Notification.Status.PENDING
        assert "Judo waiting list" in row.rendered_subject
        assert VALID["message"] in row.rendered_body
        assert "marta@example.com" in row.rendered_body

    def test_logged_in_parent_is_named_and_prefilled(self, client, contact_address):
        parent = UserFactory(first_name="Ana", last_name="Kos", email="ana@example.com")
        client.force_login(parent)

        response = client.get(reverse("contact"))
        assert "ana@example.com" in response.content.decode()

        client.post(reverse("contact"), VALID)
        # Whoever they say they are, the account they wrote from is on record.
        assert "ana@example.com" in contact_messages().get().rendered_body

    def test_honeypot_is_answered_but_not_sent(self, client, contact_address):
        response = client.post(
            reverse("contact"), {**VALID, "website": "http://spam.example"}
        )

        assert response.status_code == 302
        assert not contact_messages().exists()

    def test_incomplete_message_is_not_sent(self, client, contact_address):
        response = client.post(reverse("contact"), {**VALID, "message": ""})

        assert response.status_code == 200
        assert not contact_messages().exists()

    def test_flood_stops_after_the_hourly_allowance(self, client, contact_address):
        for _ in range(catalog_views.CONTACT_MAX_PER_HOUR + 3):
            client.post(reverse("contact"), VALID, REMOTE_ADDR="203.0.113.7")

        assert contact_messages().count() == catalog_views.CONTACT_MAX_PER_HOUR

    def test_subject_cannot_smuggle_a_header(self, client, contact_address):
        client.post(
            reverse("contact"),
            {**VALID, "subject": "Judo\nBcc: everyone@example.com"},
        )
        row = contact_messages().get()

        assert "\n" not in row.rendered_subject
        assert row.rendered_subject == "Website enquiry: Judo Bcc: everyone@example.com"
        # And it still sends, rather than sticking in the outbox.
        EmailAdapter().send(row)
        assert mail.outbox[-1].bcc == []

    def test_no_contact_address_means_no_page(self, client):
        config = SiteConfig.get()
        config.contact_email = ""
        config.save()

        assert client.get(reverse("contact")).status_code == 404
        # ...and nothing in the navigation points at it.
        assert "Contact" not in client.get(reverse("catalogue")).content.decode()

    def test_delivery_sets_reply_to_on_the_email(self, client, contact_address):
        client.post(reverse("contact"), VALID)
        row = contact_messages().get()

        EmailAdapter().send(row)

        sent = mail.outbox[-1]
        assert sent.to == [contact_address]
        assert sent.reply_to == ["marta@example.com"]
        assert sent.from_email != "marta@example.com"
