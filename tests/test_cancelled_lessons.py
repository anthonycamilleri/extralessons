"""One lesson off: the note against the date, emailed to the families.

Covers the service that batches the announcement, and each of the three doors
a date can be called off through — the class's sessions inline, the one-row
session screen, and the MCP tool.
"""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Guardian
from apps.catalog.models import generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import notify_lessons_cancelled
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def emails(**filters):
    return Notification.objects.filter(
        event=Event.LESSON_CANCELLED, channel=Notification.Channel.EMAIL, **filters
    )


def a_class_with_one_enrolled_child(capacity=5, **overrides):
    """A published class with lessons generated and one confirmed place."""
    cls = ActivityClassFactory(capacity=capacity, **overrides)
    generate_sessions(cls)
    enrollment = services.approve_request(
        services.register(ChildFactory(first_name="Ann"), cls), AdminFactory()
    )
    Notification.objects.all().delete()
    return cls, enrollment


def _written(date):
    """A date as the emails write it, spelled out independently of the app."""
    return date.strftime("%A %-d %B")


def future_sessions(cls):
    today = timezone.localdate()
    return list(cls.sessions.filter(date__gte=today, cancelled=False).order_by("date"))


class TestTheAnnouncement:
    def test_the_note_travels_as_the_reason(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        session = future_sessions(cls)[0]
        session.cancelled, session.notes = True, "Coach away"
        session.save()

        assert notify_lessons_cancelled(cls, [session]).children == 1

        row = emails(enrollment=enrollment).get()
        assert "Coach away" in row.rendered_body
        assert session.date.strftime("%A %-d %B") in row.rendered_subject
        assert session.date.strftime("%A %-d %B") in row.rendered_body
        assert cls.title in row.rendered_subject
        # A single date reads as one day, not a list of them.
        assert "that day" in row.rendered_body
        assert "· " not in row.rendered_body

    def test_a_batch_is_one_email_listing_every_date(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        off = future_sessions(cls)[:3]
        assert len(off) == 3
        for session in off:
            session.cancelled, session.notes = True, "Hall booked for exams"
            session.save()

        notify_lessons_cancelled(cls, off)

        row = emails(enrollment=enrollment).get()  # one email, not three
        for session in off:
            assert f"· {session.date.strftime('%A %-d %B')}" in row.rendered_body
        assert row.rendered_body.count("Hall booked for exams") == 1
        assert "on those days" in row.rendered_body
        # The subject leads with the nearest date and counts the rest.
        assert off[0].date.strftime("%A %-d %B") in row.rendered_subject
        assert "2 more dates" in row.rendered_subject

    def test_differing_notes_are_listed_against_their_own_dates(self):
        cls, _ = a_class_with_one_enrolled_child()
        first, second = future_sessions(cls)[:2]
        first.cancelled, first.notes = True, "Coach away"
        first.save()
        second.cancelled, second.notes = True, "Hall booked"
        second.save()

        notify_lessons_cancelled(cls, [first, second])

        body = emails().first().rendered_body
        assert f"{first.date.strftime('%A %-d %B')} — Coach away" in body
        assert f"{second.date.strftime('%A %-d %B')} — Hall booked" in body
        assert "The reason we've been given" not in body

    def test_a_date_with_no_note_still_announces_the_date(self):
        cls, _ = a_class_with_one_enrolled_child()
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        notify_lessons_cancelled(cls, [session])

        body = emails().first().rendered_body
        assert session.date.strftime("%A %-d %B") in body
        assert "The reason we've been given" not in body

    def test_every_guardian_of_the_child_is_told(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        Guardian.objects.create(child=enrollment.child, user=UserFactory())
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        # One child, so one notification pass — but both its guardians get mail.
        assert notify_lessons_cancelled(cls, [session]).children == 1
        assert emails(enrollment=enrollment).count() == 2

    def test_a_past_date_announces_nothing(self):
        cls, _ = a_class_with_one_enrolled_child()
        today = timezone.localdate()
        past = cls.sessions.filter(date__lt=today).first()
        assert past is not None, "the factory term starts a week ago"
        past.cancelled, past.notes = True, "Tidying the calendar"
        past.save()

        assert notify_lessons_cancelled(cls, [past]) == (0, 0)
        assert not emails().exists()

    def test_a_mixed_batch_counts_only_what_it_announced(self):
        """Ticking last week's date alongside next week's announces one date.

        The count the office is shown has to be the one that went out, not the
        number of rows that changed.
        """
        cls, _ = a_class_with_one_enrolled_child()
        today = timezone.localdate()
        past = cls.sessions.filter(date__lt=today).first()
        future = future_sessions(cls)[0]
        for session in (past, future):
            session.cancelled, session.notes = True, "Coach away"
            session.save()

        notice = notify_lessons_cancelled(cls, [past, future])

        assert notice == (1, 1)
        body = emails().get().rendered_body
        assert _written(future.date) in body
        assert _written(past.date) not in body

    def test_only_confirmed_places_are_told(self):
        """A child on the waiting list has no lesson to miss."""
        cls, enrolled = a_class_with_one_enrolled_child(capacity=1)
        waiting = services.approve_request(
            services.register(ChildFactory(first_name="Wes"), cls), AdminFactory()
        )
        assert waiting.status == Enrollment.Status.WAITLISTED
        Notification.objects.all().delete()
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        assert notify_lessons_cancelled(cls, [session]).children == 1
        assert emails(enrollment=enrolled).exists()
        assert not emails(enrollment=waiting).exists()


class TestTheAdminScreens:
    def test_ticking_cancelled_on_the_sessions_inline_emails_the_families(
        self, admin_client
    ):
        cls, enrollment = a_class_with_one_enrolled_child()
        sessions = future_sessions(cls)
        off = sessions[0]

        form = {
            "provider": cls.provider_id,
            "term": cls.term_id,
            "title": cls.title,
            "slug": cls.slug,
            "description": cls.description,
            "extra_details": "",
            "age_min": cls.age_min,
            "age_max": cls.age_max,
            "capacity": cls.capacity,
            "weekday": cls.weekday,
            "start_time": "15:00:00",
            "end_time": "16:00:00",
            "location": "",
            "sessions-TOTAL_FORMS": len(sessions),
            "sessions-INITIAL_FORMS": len(sessions),
        }
        for i, session in enumerate(sessions):
            form[f"sessions-{i}-id"] = session.pk
            form[f"sessions-{i}-activity_class"] = cls.pk
            form[f"sessions-{i}-date"] = session.date.isoformat()
            form[f"sessions-{i}-notes"] = ""
        form["sessions-0-cancelled"] = "on"
        form["sessions-0-notes"] = "Coach away"

        response = admin_client.post(
            reverse("admin:catalog_activityclass_change", args=[cls.pk]), form
        )

        assert response.status_code == 302, response.context["errors"]
        off.refresh_from_db()
        assert off.cancelled
        row = emails(enrollment=enrollment).get()
        assert "Coach away" in row.rendered_body
        assert off.date.strftime("%A %-d %B") in row.rendered_subject

    def test_saving_the_inline_again_does_not_email_twice(self, admin_client):
        """A second save with the same dates off is not a second announcement."""
        cls, enrollment = a_class_with_one_enrolled_child()
        session = future_sessions(cls)[0]
        session.cancelled, session.notes = True, "Coach away"
        session.save()
        notify_lessons_cancelled(cls, [session])
        assert emails().count() == 1

        sessions = list(cls.sessions.order_by("date"))
        form = {
            "provider": cls.provider_id,
            "term": cls.term_id,
            "title": "Renamed",
            "slug": cls.slug,
            "description": cls.description,
            "extra_details": "",
            "age_min": cls.age_min,
            "age_max": cls.age_max,
            "capacity": cls.capacity,
            "weekday": cls.weekday,
            "start_time": "15:00:00",
            "end_time": "16:00:00",
            "location": "",
            "sessions-TOTAL_FORMS": len(sessions),
            "sessions-INITIAL_FORMS": len(sessions),
        }
        for i, existing in enumerate(sessions):
            form[f"sessions-{i}-id"] = existing.pk
            form[f"sessions-{i}-activity_class"] = cls.pk
            form[f"sessions-{i}-date"] = existing.date.isoformat()
            form[f"sessions-{i}-notes"] = existing.notes
            if existing.cancelled:
                form[f"sessions-{i}-cancelled"] = "on"

        admin_client.post(
            reverse("admin:catalog_activityclass_change", args=[cls.pk]), form
        )

        assert emails().count() == 1

    def test_the_one_row_session_screen_emails_too(self, admin_client):
        cls, enrollment = a_class_with_one_enrolled_child()
        session = future_sessions(cls)[0]

        response = admin_client.post(
            reverse("admin:catalog_classsession_change", args=[session.pk]),
            {
                "activity_class": cls.pk,
                "date": session.date.isoformat(),
                "cancelled": "on",
                "notes": "Snow",
            },
        )

        assert response.status_code == 302
        row = emails(enrollment=enrollment).get()
        assert "Snow" in row.rendered_body


class TestTheMcpTool:
    def test_cancel_sessions_emails_the_families_and_reports_it(self):
        from apps.catalog import mcp_server as tools

        cls, enrollment = a_class_with_one_enrolled_child()
        off = [s.date.isoformat() for s in future_sessions(cls)[:2]]

        result = tools.cancel_sessions(cls.pk, off, notes="Coach away")

        assert (result["children_notified"], result["dates_announced"]) == (1, 2)
        row = emails(enrollment=enrollment).get()
        assert "Coach away" in row.rendered_body
        for date in off:
            assert datetime.date.fromisoformat(date).strftime("%A %-d %B") in row.rendered_body

    def test_cancelling_an_already_cancelled_date_says_nothing_again(self):
        from apps.catalog import mcp_server as tools

        cls, _ = a_class_with_one_enrolled_child()
        date = future_sessions(cls)[0].date.isoformat()
        tools.cancel_sessions(cls.pk, [date], notes="Coach away")
        assert emails().count() == 1

        result = tools.cancel_sessions(cls.pk, [date], notes="Coach away")

        assert result["children_notified"] == 0
        assert emails().count() == 1
