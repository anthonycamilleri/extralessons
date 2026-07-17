"""End-to-end walk through the whole product flow, driven through the views."""
import pytest
from django.core import mail
from django.urls import reverse

from apps.catalog.models import generate_sessions
from apps.enrollments.models import Attendance, Enrollment
from apps.notifications import worker
from apps.notifications.models import Event, Notification

from .factories import ActivityClassFactory, AdminFactory, ProviderUserFactory

pytestmark = pytest.mark.django_db


def test_full_lifecycle(client):
    admin = AdminFactory()
    provider_user = ProviderUserFactory()
    cls = ActivityClassFactory(title="Chess Club", capacity=1)
    cls.provider.members.add(provider_user)
    generate_sessions(cls)

    # --- Parent A signs up, adds a child, requests a place -----------------
    client.post(
        reverse("signup"),
        {
            "email": "alice@family.test",
            "first_name": "Alice",
            "last_name": "Family",
            "phone_e164": "",
            "password1": "very-s3cret-pass",
            "password2": "very-s3cret-pass",
        },
    )
    client.post(
        reverse("child_add"),
        {"first_name": "Ann", "last_name": "Family", "date_of_birth": "2018-01-15", "notes": ""},
    )
    from apps.accounts.models import Child, User

    ann = Child.objects.get(first_name="Ann")
    client.post(reverse("enroll", args=[cls.pk]), {"child": ann.pk})
    request_a = Enrollment.objects.get(child=ann)
    assert request_a.status == Enrollment.Status.REQUESTED
    client.post(reverse("logout"))

    # --- Admin approves: Ann takes the only seat ----------------------------
    client.force_login(admin)
    client.post(reverse("admintools_request_approve", args=[request_a.pk]))
    request_a.refresh_from_db()
    assert request_a.status == Enrollment.Status.ENROLLED
    client.post(reverse("logout"))

    # --- Parent B signs up; their child ends up waitlisted ------------------
    client.post(
        reverse("signup"),
        {
            "email": "bob@family.test",
            "first_name": "Bob",
            "last_name": "Family",
            "phone_e164": "",
            "password1": "very-s3cret-pass",
            "password2": "very-s3cret-pass",
        },
    )
    client.post(
        reverse("child_add"),
        {"first_name": "Ben", "last_name": "Family", "date_of_birth": "2017-06-01", "notes": ""},
    )
    ben = Child.objects.get(first_name="Ben")
    client.post(reverse("enroll", args=[cls.pk]), {"child": ben.pk})
    request_b = Enrollment.objects.get(child=ben)
    client.post(reverse("logout"))

    client.force_login(admin)
    client.post(reverse("admintools_request_approve", args=[request_b.pk]))
    request_b.refresh_from_db()
    assert request_b.status == Enrollment.Status.WAITLISTED
    client.post(reverse("logout"))

    # --- Parent A cancels; admin is alerted and offers Ben the seat ---------
    alice = User.objects.get(email="alice@family.test")
    client.force_login(alice)
    client.post(reverse("enrollment_cancel", args=[request_a.pk]))
    client.post(reverse("logout"))
    assert Notification.objects.filter(event=Event.ADMIN_SEAT_FREED).exists()

    client.force_login(admin)
    client.post(reverse("admintools_waitlist_offer", args=[request_b.pk]))
    request_b.refresh_from_db()
    assert request_b.status == Enrollment.Status.OFFERED
    client.post(reverse("logout"))

    # --- Parent B confirms the offer ----------------------------------------
    bob = User.objects.get(email="bob@family.test")
    client.force_login(bob)
    client.post(reverse("offer_confirm", args=[request_b.pk]))
    request_b.refresh_from_db()
    assert request_b.status == Enrollment.Status.ENROLLED
    client.post(reverse("logout"))

    # --- Provider sees Ben on the roster and takes attendance ---------------
    client.force_login(provider_user)
    roster_page = client.get(reverse("provider_class", args=[cls.pk]))
    assert "Ben Family" in roster_page.content.decode()

    session = cls.sessions.first()
    client.post(
        reverse("provider_attendance", args=[cls.pk, session.pk]),
        {"present": [ben.pk]},
    )
    assert Attendance.objects.get(session=session, child=ben).present is True

    # --- Provider messages the class; worker delivers everything ------------
    client.post(
        reverse("provider_broadcast"),
        {"classes": [cls.pk], "subject": "First session!", "body": "See you Monday."},
    )
    client.post(reverse("logout"))

    while worker.run_once():
        pass

    assert not Notification.objects.filter(status=Notification.Status.PENDING).exists()
    subjects = [m.subject for m in mail.outbox]
    assert any("First session!" in s for s in subjects)
    # Bob got: request receipt, waitlisted, offer, confirmation, broadcast
    bob_mail = [m for m in mail.outbox if "bob@family.test" in m.to]
    assert len(bob_mail) >= 5
