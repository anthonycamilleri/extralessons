"""The office accepts an outstanding offer for a family that said yes by
phone or email, from the roster's Outstanding offers table."""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Event, Notification

from .factories import ActivityClassFactory, AdminFactory, ChildFactory, SuperAdminFactory

pytestmark = pytest.mark.django_db


def offered(admin):
    cls = ActivityClassFactory(capacity=0)
    waiting = services.approve_request(services.register(ChildFactory(), cls), admin)
    cls.capacity = 1
    cls.save()
    return services.offer_seat(waiting, admin)


def accept_url(enrollment):
    return reverse("admin:enrollments_enrollment_accept_offer", args=[enrollment.pk])


def roster_url(enrollment):
    return reverse(
        "admin:catalog_activityclass_roster", kwargs={"object_id": enrollment.activity_class_id}
    )


def test_roster_shows_the_button_on_each_offer(client):
    admin = SuperAdminFactory()
    offer = offered(admin)
    client.force_login(admin)

    content = client.get(roster_url(offer)).content.decode()

    assert accept_url(offer) in content
    assert "Accept for family" in content


def test_accepting_enrols_the_child_and_tells_the_family(client):
    admin = SuperAdminFactory()
    offer = offered(admin)
    Notification.objects.all().delete()
    client.force_login(admin)

    response = client.post(accept_url(offer), {"next": roster_url(offer)}, follow=True)

    offer.refresh_from_db()
    assert offer.status == Enrollment.Status.ENROLLED
    assert offer.promoted_from_waitlist
    assert offer.decided_by == admin
    assert Notification.objects.filter(event=Event.REGISTRATION_CONFIRMED).exists()
    assert response.redirect_chain[-1][0] == roster_url(offer)
    assert f"{offer.child.full_name} enrolled in" in response.content.decode()


def test_an_expired_offer_cannot_be_accepted(client):
    admin = SuperAdminFactory()
    offer = offered(admin)
    Enrollment.objects.filter(pk=offer.pk).update(
        offer_expires_at=timezone.now() - datetime.timedelta(minutes=1)
    )
    client.force_login(admin)

    response = client.post(accept_url(offer), follow=True)

    offer.refresh_from_db()
    assert offer.status == Enrollment.Status.OFFERED
    assert "This offer has expired." in response.content.decode()


def test_get_is_not_allowed(client):
    admin = SuperAdminFactory()
    offer = offered(admin)
    client.force_login(admin)

    assert client.get(accept_url(offer)).status_code == 405


def test_admin_cannot_accept_an_offer_in_another_class(client):
    offer = offered(SuperAdminFactory())
    outsider = AdminFactory()
    outsider.managed_classes.add(ActivityClassFactory())
    client.force_login(outsider)

    assert client.post(accept_url(offer)).status_code == 404
    offer.refresh_from_db()
    assert offer.status == Enrollment.Status.OFFERED
