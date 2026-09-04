"""The reworded default emails (migration 0005) and the sender name."""
import importlib

import pytest
from django.core import mail

from apps.accounts.models import SiteConfig
from apps.enrollments import services as enrollment_services
from apps.notifications import worker
from apps.notifications.models import Event, Notification, NotificationTemplate

from .factories import ActivityClassFactory, ChildFactory, UserFactory

pytestmark = pytest.mark.django_db

OLD = importlib.import_module("apps.notifications.migrations.0002_default_templates")
NEW = importlib.import_module("apps.notifications.migrations.0005_friendly_templates")
# Events introduced after the rewording seed their own default, in the
# migration that added them.
LEAVING = importlib.import_module("apps.notifications.migrations.0008_cancellation_events")
LATER = {
    importlib.import_module(
        "apps.notifications.migrations.0006_contact_form_messages"
    ).EVENT,
    *LEAVING.TEMPLATES,
}


def test_every_event_has_a_default_template_row():
    """Whatever seeded it, no event may reach production without a template:
    queueing one silently produces nothing at all."""
    assert {row.event for row in NotificationTemplate.objects.all()} == {
        e.value for e in Event
    }


def test_every_event_has_a_reworded_default():
    assert set(NEW.TEMPLATES) == set(OLD.TEMPLATES) == {e.value for e in Event} - LATER
    for event, (subject, body) in NEW.TEMPLATES.items():
        assert "Dear " not in body, event
        if not event.startswith("ADMIN_"):
            assert "{{ sender_name }}" in body, event


def test_leaving_templates_speak_like_the_others():
    for event, (subject, body) in LEAVING.TEMPLATES.items():
        assert "Dear " not in body, event
        if not event.startswith("ADMIN_"):
            assert "{{ sender_name }}" in body, event
            assert "Hi {{ parent_first_name }}" in body, event


def test_school_cancellation_wording_is_narrowed_only_if_untouched():
    """SUBSCRIPTION_CANCELLED now means 'the school cancelled'; the migration
    rewrites 0005's wording to say so, and leaves an admin's own alone."""
    from django.apps import apps

    row = NotificationTemplate.objects.get(event=Event.SUBSCRIPTION_CANCELLED)
    assert "cancelled by the PTA" in row.email_body  # the seeded state after 0008
    row.email_body = "Our own wording"
    row.save()
    LEAVING.create_templates(apps, None)
    assert NotificationTemplate.objects.get(event=Event.SUBSCRIPTION_CANCELLED).email_body == "Our own wording"

    row.email_subject, row.email_body = NEW.TEMPLATES["SUBSCRIPTION_CANCELLED"]
    row.save()
    LEAVING.create_templates(apps, None)
    assert (
        NotificationTemplate.objects.get(event=Event.SUBSCRIPTION_CANCELLED).email_body
        == LEAVING.SCHOOL_CANCELLED[1]
    )


def test_receipt_is_signed_by_the_pta(client):
    parent = UserFactory(first_name="Paula", last_name="Parent")
    enrollment_services.register(ChildFactory(parent=parent), ActivityClassFactory(title="Judo"))
    row = Notification.objects.get(
        event=Event.ENROLLMENT_REQUESTED, channel=Notification.Channel.EMAIL
    )
    assert row.rendered_subject.startswith("We've got")
    assert "Hi Paula," in row.rendered_body
    assert "You'll hear from us shortly" in row.rendered_body
    assert "Warm regards,\nEuropean School PTA" in row.rendered_body
    assert "Judo" in row.rendered_body


def test_sender_name_is_configurable():
    config = SiteConfig.get()
    config.sender_name = "Friends of St Example"
    config.contact_email = "pta@example.org"
    config.save()
    enrollment_services.register(ChildFactory(), ActivityClassFactory())
    row = Notification.objects.get(
        event=Event.ENROLLMENT_REQUESTED, channel=Notification.Channel.EMAIL
    )
    assert "Warm regards,\nFriends of St Example" in row.rendered_body
    assert "write to pta@example.org" in row.rendered_body
    assert "{{" not in row.rendered_body  # nothing left unrendered

    while worker.run_once():
        pass
    assert mail.outbox and "Friends of St Example" in mail.outbox[0].body


def test_migration_leaves_customised_rows_alone():
    """Re-running the rewording (as a fresh deploy would) never clobbers an admin's edit."""
    from django.apps import apps

    row = NotificationTemplate.objects.get(event=Event.WAITLISTED)
    row.email_body = "Our own wording"
    row.save()
    untouched = NotificationTemplate.objects.get(event=Event.OFFER_EXPIRED)
    untouched.email_subject, untouched.email_body = OLD.TEMPLATES["OFFER_EXPIRED"]
    untouched.save()

    NEW.forwards(apps, None)

    assert NotificationTemplate.objects.get(event=Event.WAITLISTED).email_body == "Our own wording"
    assert (
        NotificationTemplate.objects.get(event=Event.OFFER_EXPIRED).email_body
        == NEW.TEMPLATES["OFFER_EXPIRED"][1]
    )
