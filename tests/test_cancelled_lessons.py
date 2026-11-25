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

        assert notify_lessons_cancelled(cls, [past]) == (0, 0, 0)
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

        assert notice == (1, 1, 0)
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


def teacher_emails(**filters):
    return Notification.objects.filter(event=Event.INSTRUCTOR_LESSON_CANCELLED, **filters)


def assign_teacher(cls, **user_fields):
    from apps.catalog.models import Instructor

    user = UserFactory(**user_fields)
    cls.instructors.add(Instructor.objects.create(provider=cls.provider, user=user))
    return user


class TestTheTeacherIsTold:
    def test_the_assigned_instructor_gets_the_dates_and_the_reason(self):
        cls, _ = a_class_with_one_enrolled_child()
        teacher = assign_teacher(cls, first_name="Tess")
        off = future_sessions(cls)[:2]
        for session in off:
            session.cancelled, session.notes = True, "Hall booked"
            session.save()

        notice = notify_lessons_cancelled(cls, off)

        assert notice == (1, 2, 1)
        row = teacher_emails(recipient=teacher).get()  # one email for the batch
        assert row.channel == Notification.Channel.EMAIL
        assert row.enrollment is None
        assert row.rendered_body.startswith("Hi Tess,")
        assert "Hall booked" in row.rendered_body
        for session in off:
            assert _written(session.date) in row.rendered_body
        assert "family of the 1 enrolled child know" in row.rendered_body
        assert cls.title in row.rendered_subject
        assert f"/provider/classes/{cls.pk}/" in row.rendered_body

    def test_every_assigned_instructor_is_told(self):
        cls, _ = a_class_with_one_enrolled_child()
        assign_teacher(cls)
        assign_teacher(cls)
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        assert notify_lessons_cancelled(cls, [session]).teachers == 2
        assert teacher_emails().count() == 2

    def test_with_nobody_assigned_the_provider_address_is_told(self):
        cls, _ = a_class_with_one_enrolled_child()
        cls.provider.contact_email = "coach@example.com"
        cls.provider.save()
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        assert notify_lessons_cancelled(cls, [session]).teachers == 1
        row = teacher_emails().get()
        assert row.recipient is None
        assert row.recipient_email == "coach@example.com"
        assert row.rendered_body.startswith(f"Hi {cls.provider.name},")

    def test_with_nobody_to_tell_the_families_still_hear(self):
        cls, _ = a_class_with_one_enrolled_child()
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        assert notify_lessons_cancelled(cls, [session]) == (1, 1, 0)
        assert not teacher_emails().exists()

    def test_the_teacher_hears_even_when_nobody_is_enrolled(self):
        cls = ActivityClassFactory()
        generate_sessions(cls)
        assign_teacher(cls)
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        assert notify_lessons_cancelled(cls, [session]) == (0, 1, 1)
        assert "No children are enrolled" in teacher_emails().get().rendered_body

    def test_an_instructor_who_turned_email_off_is_skipped(self):
        cls, _ = a_class_with_one_enrolled_child()
        teacher = assign_teacher(cls, notify_email=False)
        session = future_sessions(cls)[0]
        session.cancelled = True
        session.save()

        notify_lessons_cancelled(cls, [session])

        assert teacher_emails(recipient=teacher).get().status == Notification.Status.SKIPPED

    def test_the_admin_screen_says_the_teacher_was_told(self, admin_client):
        cls, _ = a_class_with_one_enrolled_child()
        assign_teacher(cls)
        session = future_sessions(cls)[0]

        response = admin_client.post(
            reverse("admin:catalog_classsession_change", args=[session.pk]),
            {
                "activity_class": cls.pk,
                "date": session.date.isoformat(),
                "cancelled": "on",
                "notes": "Snow",
            },
            follow=True,
        )

        text = response.content.decode()
        assert "the families of 1 child(ren) and 1 teacher(s)" in text
        assert teacher_emails().exists()

    def test_the_mcp_tool_reports_the_teachers_told(self):
        from apps.catalog import mcp_server as tools

        cls, _ = a_class_with_one_enrolled_child()
        assign_teacher(cls)
        date = future_sessions(cls)[0].date.isoformat()

        result = tools.cancel_sessions(cls.pk, [date], notes="Coach away")

        assert result["teachers_notified"] == 1
        assert "Coach away" in teacher_emails().get().rendered_body


def reminders(**filters):
    return Notification.objects.filter(
        event=Event.LESSON_CANCELLED_REMINDER, channel=Notification.Channel.EMAIL, **filters
    )


def teacher_reminders(**filters):
    return Notification.objects.filter(event=Event.INSTRUCTOR_LESSON_REMINDER, **filters)


def called_off(cls, date, *, days_before, notes="Coach away"):
    """A lesson on `date`, cancelled `days_before` days ahead of it."""
    from apps.catalog.models import ClassSession

    session, _ = ClassSession.objects.get_or_create(activity_class=cls, date=date)
    session.cancelled, session.notes = True, notes
    session.save()
    when = timezone.make_aware(
        datetime.datetime.combine(date - datetime.timedelta(days=days_before), datetime.time(10))
    )
    ClassSession.objects.filter(pk=session.pk).update(cancelled_at=when)
    session.refresh_from_db()
    return session


class TestTheDayBeforeReminder:
    def test_families_and_teacher_are_reminded_the_day_before(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        teacher = assign_teacher(cls, first_name="Tess")
        session = future_sessions(cls)[0]
        session = called_off(cls, session.date, days_before=5)
        day_before = session.date - datetime.timedelta(days=1)

        assert services.send_lesson_reminders(today=day_before) == 1

        row = reminders(enrollment=enrollment).get()
        assert "Reminder" in row.rendered_subject
        assert "tomorrow" in row.rendered_subject
        assert _written(session.date) in row.rendered_body
        assert "Coach away" in row.rendered_body
        assert "Ann" in row.rendered_body
        teacher_row = teacher_reminders(recipient=teacher).get()
        assert teacher_row.rendered_body.startswith("Hi Tess,")
        assert "cancelled tomorrow" in teacher_row.rendered_body
        session.refresh_from_db()
        assert session.reminder_sent_at is not None

    def test_a_date_is_reminded_only_once(self):
        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)
        day_before = session.date - datetime.timedelta(days=1)

        services.send_lesson_reminders(today=day_before)
        assert services.send_lesson_reminders(today=day_before) == 0

        assert reminders().count() == 1

    def test_dates_further_out_wait_for_their_own_day_before(self):
        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=10)

        assert services.send_lesson_reminders(
            today=session.date - datetime.timedelta(days=3)
        ) == 0
        assert not reminders().exists()

    def test_a_date_cancelled_on_the_day_before_is_not_reminded(self):
        """The announcement went out hours ago: a reminder would be a repeat."""
        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=1)

        assert services.send_lesson_reminders(
            today=session.date - datetime.timedelta(days=1)
        ) == 0
        assert services.send_lesson_reminders(today=session.date) == 0
        assert not reminders().exists()

    def test_a_missed_night_is_caught_up_on_the_morning_of_the_lesson(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)

        assert services.send_lesson_reminders(today=session.date) == 1

        assert "today" in reminders(enrollment=enrollment).get().rendered_subject

    def test_a_date_put_back_on_is_not_reminded(self):
        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)
        session.cancelled = False
        session.save(update_fields=["cancelled"])

        session.refresh_from_db()
        assert session.cancelled_at is None
        assert services.send_lesson_reminders(
            today=session.date - datetime.timedelta(days=1)
        ) == 0
        assert not reminders().exists()

    def test_cancelling_again_after_a_restore_starts_afresh(self):
        from apps.catalog import mcp_server as tools

        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)
        services.send_lesson_reminders(today=session.date - datetime.timedelta(days=1))
        tools.restore_sessions(cls.pk, [session.date.isoformat()])
        session.refresh_from_db()
        assert (session.cancelled_at, session.reminder_sent_at) == (None, None)

        tools.cancel_sessions(cls.pk, [session.date.isoformat()])

        session.refresh_from_db()
        assert session.cancelled and session.cancelled_at is not None
        assert session.reminder_sent_at is None

    def test_a_date_cancelled_before_the_stamp_existed_is_reminded(self):
        from apps.catalog.models import ClassSession

        cls, _ = a_class_with_one_enrolled_child()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)
        ClassSession.objects.filter(pk=session.pk).update(cancelled_at=None)

        assert services.send_lesson_reminders(
            today=session.date - datetime.timedelta(days=1)
        ) == 1

    def test_only_confirmed_places_are_reminded(self):
        cls, enrolled = a_class_with_one_enrolled_child(capacity=1)
        waiting = services.approve_request(
            services.register(ChildFactory(), cls), AdminFactory()
        )
        Notification.objects.all().delete()
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)

        services.send_lesson_reminders(today=session.date - datetime.timedelta(days=1))

        assert reminders(enrollment=enrolled).exists()
        assert not reminders(enrollment=waiting).exists()

    def test_a_cancelled_class_sends_no_reminders(self):
        cls, _ = a_class_with_one_enrolled_child()
        assign_teacher(cls)
        session = called_off(cls, future_sessions(cls)[0].date, days_before=5)
        services.cancel_class(cls)

        assert services.send_lesson_reminders(
            today=session.date - datetime.timedelta(days=1)
        ) == 0
        assert not teacher_reminders().exists()

    def test_the_nightly_notifier_queues_and_delivers_tomorrows_reminder(self, mailoutbox):
        """End to end, on the real calendar: nothing runs between the
        cancellation and the scheduled job, which does both halves."""
        from apps.notifications import worker

        cls, enrollment = a_class_with_one_enrolled_child()
        teacher = assign_teacher(cls)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        called_off(cls, tomorrow, days_before=4)
        mailoutbox.clear()

        worker.run_once()

        to = {address for message in mailoutbox for address in message.to}
        assert enrollment.child.guardians.get().email in to
        assert teacher.email in to
        assert reminders().exists()
        assert all(row.status == Notification.Status.SENT for row in reminders())
