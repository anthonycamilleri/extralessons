"""Leaving a class: withdraw while the window is open, ask to cancel after it.

Covers the two-week rule end to end — the service decisions, the parent page
that shows Withdraw or Cancel accordingly, the office's confirm / keep-place
desk, and the emails each step sends.
"""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SiteConfig
from apps.catalog.models import generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    SuperAdminFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

WINDOW = 14
WITHDRAW_BUTTON = 'class="linklike small">Withdraw<'
CANCEL_BUTTON = 'class="linklike small">Cancel<'


def emails(event):
    return Notification.objects.filter(event=event, channel=Notification.Channel.EMAIL)


def enrolled(parent, cls=None, registered_days_ago=0, promoted=False):
    """A confirmed place, registered the given number of days ago."""
    cls = cls or ActivityClassFactory(capacity=5)
    enrollment = services.approve_request(
        services.register(ChildFactory(parent=parent), cls), SuperAdminFactory()
    )
    stamp = timezone.now() - datetime.timedelta(days=registered_days_ago)
    Enrollment.objects.filter(pk=enrollment.pk).update(
        created_at=stamp, approved_at=stamp, enrolled_at=stamp, promoted_from_waitlist=promoted
    )
    enrollment.refresh_from_db()
    Notification.objects.all().delete()
    return enrollment


class TestWithdrawalWindow:
    def test_confirmed_place_can_be_withdrawn_inside_the_window(self):
        e = enrolled(UserFactory(), registered_days_ago=WINDOW - 1)
        assert e.can_withdraw(WINDOW)

    def test_confirmed_place_cannot_be_withdrawn_after_the_window(self):
        e = enrolled(UserFactory(), registered_days_ago=WINDOW)
        assert not e.can_withdraw(WINDOW)

    def test_window_is_configurable(self):
        e = enrolled(UserFactory(), registered_days_ago=20)
        assert not e.can_withdraw(WINDOW)
        assert e.can_withdraw(30)

    def test_nothing_confirmed_can_always_be_withdrawn(self):
        old = timezone.now() - datetime.timedelta(days=60)
        for status in (
            Enrollment.Status.REQUESTED,
            Enrollment.Status.WAITLISTED,
            Enrollment.Status.OFFERED,
        ):
            e = Enrollment(status=status, created_at=old)
            assert e.can_withdraw(WINDOW), status
        assert not Enrollment(status=Enrollment.Status.CANCELLED, created_at=old).can_withdraw(WINDOW)

    def test_waiting_list_promotion_restarts_the_window(self):
        """Registered five weeks ago, given the seat yesterday: the family still
        gets two weeks to decide from the day the place was confirmed."""
        e = enrolled(UserFactory(), registered_days_ago=35, promoted=True)
        Enrollment.objects.filter(pk=e.pk).update(
            enrolled_at=timezone.now() - datetime.timedelta(days=1)
        )
        e.refresh_from_db()
        assert e.can_withdraw(WINDOW)
        assert e.withdrawal_deadline(WINDOW).date() == (
            timezone.now() + datetime.timedelta(days=WINDOW - 1)
        ).date()


class TestParentCancelService:
    def test_withdraw_inside_window_cancels_and_sends_receipt(self):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=3)

        result = services.parent_cancel(e, actor=parent)

        assert result.status == Enrollment.Status.CANCELLED
        assert result.cancel_reason == Enrollment.CancelReason.PARENT
        assert emails(Event.WITHDRAWN).count() == 1
        assert not emails(Event.SUBSCRIPTION_CANCELLED).exists()
        body = emails(Event.WITHDRAWN).get().rendered_body
        assert "has been withdrawn from" in body and "{{" not in body

    def test_withdraw_a_request_after_the_window_is_still_immediate(self):
        parent = UserFactory()
        e = services.register(ChildFactory(parent=parent), ActivityClassFactory())
        Enrollment.objects.filter(pk=e.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=40)
        )
        e.refresh_from_db()

        result = services.parent_cancel(e, actor=parent)

        assert result.status == Enrollment.Status.CANCELLED
        assert emails(Event.WITHDRAWN).count() == 1

    def test_after_window_files_a_request_instead(self):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)

        result = services.parent_cancel(e, actor=parent)

        assert result.status == Enrollment.Status.ENROLLED  # still holds the seat
        assert result.cancellation_requested
        assert result.cancel_requested_by == parent
        assert emails(Event.CANCELLATION_REQUESTED).count() == 1
        assert emails(Event.ADMIN_CANCELLATION_REQUESTED).count() == 1
        parent_mail = emails(Event.CANCELLATION_REQUESTED).get().rendered_body
        assert "keep attending" in parent_mail and "{{" not in parent_mail
        admin_mail = emails(Event.ADMIN_CANCELLATION_REQUESTED).get()
        assert "withdrawal period ended on" in admin_mail.rendered_body
        assert "{{" not in admin_mail.rendered_body
        assert reverse("admin:enrollments_enrollment_requests") in admin_mail.rendered_body

    def test_request_goes_to_the_classs_own_administrators(self):
        cls = ActivityClassFactory(capacity=5)
        mine, theirs = AdminFactory(), AdminFactory()
        cls.administrators.add(mine)
        parent = UserFactory()
        e = enrolled(parent, cls=cls, registered_days_ago=WINDOW + 1)

        services.parent_cancel(e, actor=parent)

        recipients = {n.recipient for n in emails(Event.ADMIN_CANCELLATION_REQUESTED)}
        assert mine in recipients and theirs not in recipients

    def test_asking_twice_is_refused(self):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        with pytest.raises(EnrollmentError, match="already asked"):
            services.request_cancellation(e, parent)

    def test_only_a_confirmed_place_takes_a_request(self):
        parent = UserFactory()
        e = services.register(ChildFactory(parent=parent), ActivityClassFactory())
        with pytest.raises(EnrollmentError, match="simply be withdrawn"):
            services.request_cancellation(e, parent)

    def test_seat_stays_counted_while_the_request_is_open(self):
        parent = UserFactory()
        cls = ActivityClassFactory(capacity=1)
        e = enrolled(parent, cls=cls, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        assert cls.places_free_now() == 0
        assert cls.places_available_now() == 0


class TestOfficeDecides:
    def test_confirm_cancels_tells_family_and_flags_freed_seat(self):
        parent = UserFactory()
        cls = ActivityClassFactory(capacity=1)
        e = enrolled(parent, cls=cls, registered_days_ago=WINDOW + 1)
        admin = SuperAdminFactory()
        services.approve_request(services.register(ChildFactory(), cls), admin)  # waitlisted
        services.parent_cancel(e, actor=parent)
        Notification.objects.all().delete()

        result = services.confirm_cancellation(e, admin)

        assert result.status == Enrollment.Status.CANCELLED
        assert result.cancel_reason == Enrollment.CancelReason.PARENT_REQUEST
        assert result.decided_by == admin
        assert emails(Event.CANCELLATION_CONFIRMED).count() == 1
        assert "As you asked" in emails(Event.CANCELLATION_CONFIRMED).get().rendered_body
        assert not emails(Event.SUBSCRIPTION_CANCELLED).exists()
        assert emails(Event.ADMIN_SEAT_FREED).exists()
        assert cls.places_free_now() == 1

    def test_keep_place_clears_the_request_and_tells_family(self):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        admin = SuperAdminFactory()
        services.parent_cancel(e, actor=parent)
        Notification.objects.all().delete()

        result = services.decline_cancellation(e, admin)

        assert result.status == Enrollment.Status.ENROLLED
        assert not result.cancellation_requested
        assert result.cancel_requested_by is None
        assert result.decided_by == admin
        assert emails(Event.CANCELLATION_DECLINED).count() == 1
        assert "kept the place" in emails(Event.CANCELLATION_DECLINED).get().rendered_body
        # The family can ask again later.
        services.request_cancellation(e, parent)

    def test_decisions_need_an_open_request(self):
        e = enrolled(UserFactory(), registered_days_ago=WINDOW + 1)
        admin = SuperAdminFactory()
        with pytest.raises(EnrollmentError, match="no open cancellation request"):
            services.confirm_cancellation(e, admin)
        with pytest.raises(EnrollmentError, match="no open cancellation request"):
            services.decline_cancellation(e, admin)

    def test_school_cancellation_keeps_its_own_email(self):
        e = enrolled(UserFactory())
        services.cancel(e, Enrollment.CancelReason.ADMIN, actor=SuperAdminFactory())
        assert emails(Event.SUBSCRIPTION_CANCELLED).count() == 1
        assert not emails(Event.WITHDRAWN).exists()


class TestFamilyPage:
    def test_shows_next_class_for_every_registration(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        running = ActivityClassFactory(title="Chess Club")  # term started a week ago
        generate_sessions(running)
        waiting = ActivityClassFactory(title="Judo", capacity=0)
        services.register(child, waiting)
        services.approve_request(services.register(child, running), SuperAdminFactory())
        generate_sessions(waiting)
        client.force_login(parent)

        content = client.get(reverse("parent_home")).content.decode()

        today = timezone.localdate()
        for cls in (running, waiting):
            upcoming = cls.sessions.filter(cancelled=False, date__gte=today).first().date
            assert upcoming.strftime("%a %-d %b") in content
        assert "Next class" in content
        assert content.count("No dates yet") == 0

    def test_coming_up_lists_this_weeks_lessons_including_cancelled_ones(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent, first_name="Ann")
        today = timezone.localdate()
        tomorrow = today + datetime.timedelta(days=1)
        admin = SuperAdminFactory()
        chess = ActivityClassFactory(title="Chess Club", weekday=today.weekday(), location="Gym")
        judo = ActivityClassFactory(title="Judo", weekday=tomorrow.weekday())
        for cls in (chess, judo):
            generate_sessions(cls)
            services.approve_request(services.register(child, cls), admin)
        off = judo.sessions.get(date=tomorrow)
        off.cancelled, off.notes = True, "Coach away"
        off.save()
        client.force_login(parent)

        content = client.get(reverse("parent_home")).content.decode()

        assert "Coming up" in content
        assert "Today · " in content and "Tomorrow · " in content
        assert "Ann" in content and "Gym" in content
        assert "No class — Coach away" in content
        assert "week-today" in content
        # A week from today is the next Chess Club: outside the window.
        assert (today + datetime.timedelta(days=7)).strftime("%A %-d %B") not in content

    def test_coming_up_only_appears_once_something_is_confirmed(self, client):
        parent = UserFactory()
        services.register(ChildFactory(parent=parent), ActivityClassFactory())
        client.force_login(parent)
        content = client.get(reverse("parent_home")).content.decode()
        assert "Coming up" not in content

    def test_withdraw_button_inside_the_window(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=2)
        client.force_login(parent)

        content = client.get(reverse("parent_home")).content.decode()

        assert WITHDRAW_BUTTON in content
        assert CANCEL_BUTTON not in content
        deadline = timezone.localtime(e.withdrawal_deadline(WINDOW))
        assert f"free until {deadline.strftime('%-d %b')}" in content
        assert "Changing your mind" in content

    def test_cancel_button_after_the_window(self, client):
        parent = UserFactory()
        enrolled(parent, registered_days_ago=WINDOW + 2)
        client.force_login(parent)

        content = client.get(reverse("parent_home")).content.decode()

        assert CANCEL_BUTTON in content
        assert WITHDRAW_BUTTON not in content

    def test_window_length_comes_from_site_configuration(self, client):
        config = SiteConfig.get()
        config.withdrawal_window_days = 30
        config.save()
        parent = UserFactory()
        enrolled(parent, registered_days_ago=WINDOW + 2)
        client.force_login(parent)

        content = client.get(reverse("parent_home")).content.decode()

        assert WITHDRAW_BUTTON in content
        assert "first <b>30 days</b>" in content

    def test_posting_inside_the_window_withdraws(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=2)
        client.force_login(parent)

        response = client.post(reverse("enrollment_cancel", args=[e.pk]), follow=True)

        e.refresh_from_db()
        assert e.status == Enrollment.Status.CANCELLED
        assert "has been withdrawn" in response.content.decode()

    def test_posting_after_the_window_asks_the_office(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 2)
        client.force_login(parent)

        response = client.post(reverse("enrollment_cancel", args=[e.pk]), follow=True)

        e.refresh_from_db()
        assert e.status == Enrollment.Status.ENROLLED
        assert e.cancellation_requested
        content = response.content.decode()
        assert "passed your request to cancel" in content
        assert "Cancellation requested" in content
        assert "Asked to cancel on" in content
        assert CANCEL_BUTTON not in content and WITHDRAW_BUTTON not in content

    def test_terms_default_explains_the_rule(self, client):
        content = client.get(reverse("terms")).content.decode()
        assert "first two weeks after registering" in content


class TestOfficeDesk:
    def test_requests_page_lists_cancellation_requests_with_actions(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        client.force_login(SuperAdminFactory())

        content = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()

        assert "Cancellation requests (1)" in content
        assert e.child.full_name in content
        assert reverse("admin:enrollments_enrollment_confirm_cancellation", args=[e.pk]) in content
        assert reverse("admin:enrollments_enrollment_keep_place", args=[e.pk]) in content

    def test_badge_counts_cancellation_requests_too(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.register(ChildFactory(parent=parent), ActivityClassFactory())  # one new request
        services.parent_cancel(e, actor=parent)
        client.force_login(SuperAdminFactory())

        response = client.get(reverse("admin:index"))

        assert response.context["pending_requests_count"] == 2

    def test_admin_only_sees_their_classes_requests(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        outsider = AdminFactory()
        outsider.managed_classes.add(ActivityClassFactory())
        client.force_login(outsider)

        content = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()

        assert "Cancellation requests (0)" in content
        response = client.post(
            reverse("admin:enrollments_enrollment_confirm_cancellation", args=[e.pk])
        )
        assert response.status_code == 404

    def test_confirm_from_the_desk(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        client.force_login(SuperAdminFactory())

        response = client.post(
            reverse("admin:enrollments_enrollment_confirm_cancellation", args=[e.pk]),
            follow=True,
        )

        e.refresh_from_db()
        assert e.status == Enrollment.Status.CANCELLED
        assert "has been cancelled; the family has been told" in response.content.decode()

    def test_keep_place_from_the_roster(self, client):
        parent = UserFactory()
        e = enrolled(parent, registered_days_ago=WINDOW + 1)
        services.parent_cancel(e, actor=parent)
        client.force_login(SuperAdminFactory())
        roster = reverse("admin:catalog_activityclass_roster", args=[e.activity_class_id])

        content = client.get(roster).content.decode()
        assert "Asked to cancel" in content

        response = client.post(
            reverse("admin:enrollments_enrollment_keep_place", args=[e.pk]),
            {"next": roster},
            follow=True,
        )

        e.refresh_from_db()
        assert e.status == Enrollment.Status.ENROLLED and not e.cancellation_requested
        assert response.redirect_chain[-1][0] == roster
        assert "keeps their place" in response.content.decode()

    def test_desk_actions_are_post_only(self, client):
        e = enrolled(UserFactory())
        client.force_login(SuperAdminFactory())
        for name in ("confirm_cancellation", "keep_place"):
            response = client.get(reverse(f"admin:enrollments_enrollment_{name}", args=[e.pk]))
            assert response.status_code == 405
