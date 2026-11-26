"""Extra lessons: a date added on top of the weekly schedule, and announced.

Covers the service, regeneration leaving the date alone, and each door an
extra lesson can come in through — the class's "Add extra lesson" page, a
row typed into the sessions inline, the one-row session screen, and the MCP
tool — plus the teacher's pages showing it.
"""
import datetime

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import ActivityClass, ClassSession, Instructor, generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import add_extra_lesson
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    HolidayFactory,
    ProviderUserFactory,
    ReadOnlyAdminFactory,
    SchoolYearFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def family_emails(**filters):
    return Notification.objects.filter(
        event=Event.LESSON_ADDED, channel=Notification.Channel.EMAIL, **filters
    )


def teacher_emails(**filters):
    return Notification.objects.filter(event=Event.INSTRUCTOR_LESSON_ADDED, **filters)


def a_class_with_one_enrolled_child(capacity=5, **overrides):
    """A published Monday class with lessons generated and one confirmed place."""
    cls = ActivityClassFactory(capacity=capacity, location="Gym", **overrides)
    generate_sessions(cls)
    enrollment = services.approve_request(
        services.register(ChildFactory(first_name="Ann"), cls), AdminFactory()
    )
    Notification.objects.all().delete()
    return cls, enrollment


def next_saturday():
    """A day the Monday class never meets, at least two days off."""
    day = timezone.localdate() + datetime.timedelta(days=2)
    return day + datetime.timedelta(days=(5 - day.weekday()) % 7)


def _written(date):
    return date.strftime("%A %-d %B")


def assign_teacher(cls, **user_fields):
    user = UserFactory(**user_fields)
    cls.instructors.add(Instructor.objects.create(provider=cls.provider, user=user))
    return user


class TestTheService:
    def test_adds_an_extra_lesson_with_its_own_time_and_place(self):
        cls, _ = a_class_with_one_enrolled_child()
        day = next_saturday()

        session, notice = add_extra_lesson(
            cls,
            day,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(11, 30),
            location="Main hall",
            notes="Rehearsal before the show",
        )

        assert session.is_extra
        assert (session.starts_at, session.ends_at) == (datetime.time(10), datetime.time(11, 30))
        assert session.place == "Main hall"
        assert notice.children == 1 and notice.dates == 1

    def test_the_family_email_carries_the_date_time_place_and_note(self):
        cls, enrollment = a_class_with_one_enrolled_child()
        day = next_saturday()

        add_extra_lesson(
            cls,
            day,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(11, 30),
            location="Main hall",
            notes="Make-up for the lesson lost to snow",
        )

        row = family_emails(enrollment=enrollment).get()
        assert _written(day) in row.rendered_subject
        assert cls.title in row.rendered_subject
        for text in (_written(day), "10:00–11:30", "Main hall", "lost to snow", "Ann"):
            assert text in row.rendered_body, text
        # It reads as an addition, not a change to the usual lessons.
        assert cls.schedule_display in row.rendered_body
        assert "an extra lesson" in row.rendered_body

    def test_the_usual_time_and_place_are_stored_as_the_usual(self):
        cls, enrollment = a_class_with_one_enrolled_child()

        session, _ = add_extra_lesson(
            cls, next_saturday(), start_time=cls.start_time, end_time=cls.end_time, location="Gym"
        )

        assert (session.start_time, session.end_time, session.location) == (None, None, "")
        assert session.time_display == "15:00–16:00"
        assert "Gym" in family_emails(enrollment=enrollment).get().rendered_body

    def test_the_teacher_is_told_how_many_families_know(self):
        cls, _ = a_class_with_one_enrolled_child()
        teacher = assign_teacher(cls, first_name="Tess")
        day = next_saturday()

        notice = add_extra_lesson(cls, day, notes="Rehearsal")[1]

        assert notice.teachers == 1
        row = teacher_emails(recipient=teacher).get()
        assert "Hi Tess" in row.rendered_body
        assert _written(day) in row.rendered_subject
        assert "Rehearsal" in row.rendered_body
        assert "family of the 1 enrolled child" in row.rendered_body

    def test_only_confirmed_places_are_told(self):
        cls, enrolled = a_class_with_one_enrolled_child(capacity=1)
        waiting = services.approve_request(
            services.register(ChildFactory(first_name="Wes"), cls), AdminFactory()
        )
        assert waiting.status == Enrollment.Status.WAITLISTED
        Notification.objects.all().delete()

        add_extra_lesson(cls, next_saturday())

        assert family_emails(enrollment=enrolled).exists()
        assert not family_emails(enrollment=waiting).exists()

    def test_notify_false_adds_it_quietly(self):
        cls, _ = a_class_with_one_enrolled_child()
        assign_teacher(cls)

        session, notice = add_extra_lesson(cls, next_saturday(), notify=False)

        assert session.pk and notice == (0, 0, 0)
        assert not Notification.objects.exists()

    def test_a_date_the_class_already_has_is_refused(self):
        cls, _ = a_class_with_one_enrolled_child()
        regular = cls.sessions.filter(date__gte=timezone.localdate()).first()

        with pytest.raises(ValidationError, match="already has a lesson"):
            add_extra_lesson(cls, regular.date)

        regular.cancelled = True
        regular.save()
        with pytest.raises(ValidationError, match="Untick"):
            add_extra_lesson(cls, regular.date)
        assert not Notification.objects.filter(event=Event.LESSON_ADDED).exists()

    def test_a_past_date_is_refused(self):
        cls, _ = a_class_with_one_enrolled_child()
        with pytest.raises(ValidationError, match="past"):
            add_extra_lesson(cls, timezone.localdate() - datetime.timedelta(days=1))

    @pytest.mark.parametrize(
        "status", [ActivityClass.Status.CANCELLED, ActivityClass.Status.ARCHIVED]
    )
    def test_a_class_that_is_not_running_is_refused(self, status):
        cls = ActivityClassFactory(status=status)
        with pytest.raises(ValidationError, match="cannot be added"):
            add_extra_lesson(cls, next_saturday())

    def test_times_must_add_up(self):
        cls, _ = a_class_with_one_enrolled_child()
        with pytest.raises(ValidationError):
            add_extra_lesson(
                cls,
                next_saturday(),
                start_time=datetime.time(11),
                end_time=datetime.time(10),
            )


class TestRegeneration:
    def test_keeps_an_extra_lesson_off_the_weekly_pattern(self):
        cls, _ = a_class_with_one_enrolled_child()
        session, _ = add_extra_lesson(cls, next_saturday())

        generate_sessions(cls)

        assert ClassSession.objects.filter(pk=session.pk).exists()

    def test_keeps_an_extra_lesson_inside_a_holiday(self):
        year = SchoolYearFactory()
        cls, _ = a_class_with_one_enrolled_child()
        cls.term.school_year = year
        cls.term.save()
        day = next_saturday()
        session, _ = add_extra_lesson(cls, day)
        HolidayFactory(school_year=year, start_date=day, end_date=day + datetime.timedelta(days=3))

        generate_sessions(cls)

        assert ClassSession.objects.filter(pk=session.pk).exists()


class TestTheAddLessonPage:
    def url(self, cls):
        return reverse("admin:catalog_activityclass_add_lesson", args=[cls.pk])

    def test_opens_with_the_class_time_and_place_filled_in(self, admin_client):
        cls, _ = a_class_with_one_enrolled_child()

        response = admin_client.get(self.url(cls))

        assert response.status_code == 200
        form = response.context["form"]
        assert form.initial["start_time"] == cls.start_time
        assert form.initial["location"] == "Gym"

    def test_adds_the_lesson_and_emails_everyone(self, admin_client):
        cls, enrollment = a_class_with_one_enrolled_child()
        assign_teacher(cls)
        day = next_saturday()

        response = admin_client.post(
            self.url(cls),
            {
                "date": day.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
                "location": "Main hall",
                "notes": "Make-up lesson",
                "notify": "on",
            },
            follow=True,
        )

        session = cls.sessions.get(date=day)
        assert session.is_extra and session.place == "Main hall"
        assert family_emails(enrollment=enrollment).exists()
        assert teacher_emails().count() == 1
        text = " ".join(str(m) for m in response.context["messages"])
        assert "Extra lesson added" in text and "1 child(ren)" in text and "1 teacher(s)" in text

    def test_unticking_notify_emails_nobody(self, admin_client):
        cls, _ = a_class_with_one_enrolled_child()
        day = next_saturday()

        admin_client.post(
            self.url(cls),
            {"date": day.isoformat(), "start_time": "15:00", "end_time": "16:00", "location": "Gym"},
        )

        assert cls.sessions.get(date=day).is_extra
        assert not Notification.objects.exists()

    def test_a_refused_date_comes_back_on_the_form(self, admin_client):
        cls, _ = a_class_with_one_enrolled_child()
        regular = cls.sessions.filter(date__gte=timezone.localdate()).first()

        response = admin_client.post(
            self.url(cls),
            {
                "date": regular.date.isoformat(),
                "start_time": "15:00",
                "end_time": "16:00",
                "notify": "on",
            },
        )

        assert response.status_code == 200
        assert "already has a lesson" in str(response.context["form"].errors)

    def test_a_read_only_admin_cannot_add(self, client):
        cls, _ = a_class_with_one_enrolled_child()
        client.force_login(ReadOnlyAdminFactory())
        assert client.get(self.url(cls)).status_code == 403

    def test_an_admin_cannot_add_to_a_class_they_do_not_look_after(self, client):
        cls, _ = a_class_with_one_enrolled_child()
        client.force_login(AdminFactory())
        assert client.get(self.url(cls)).status_code == 404

    def test_the_class_form_links_to_it(self, admin_client):
        cls, _ = a_class_with_one_enrolled_child()
        response = admin_client.get(reverse("admin:catalog_activityclass_change", args=[cls.pk]))
        assert self.url(cls) in response.content.decode()


class TestTheOtherAdminDoors:
    def test_a_row_typed_into_the_inline_is_an_extra_lesson(self, admin_client):
        cls, enrollment = a_class_with_one_enrolled_child()
        sessions = list(cls.sessions.order_by("date"))
        day = next_saturday()
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
            "location": "Gym",
            "sessions-TOTAL_FORMS": len(sessions) + 1,
            "sessions-INITIAL_FORMS": len(sessions),
        }
        for i, session in enumerate(sessions):
            form[f"sessions-{i}-id"] = session.pk
            form[f"sessions-{i}-activity_class"] = cls.pk
            form[f"sessions-{i}-date"] = session.date.isoformat()
            form[f"sessions-{i}-notes"] = ""
        new = len(sessions)
        form[f"sessions-{new}-activity_class"] = cls.pk
        form[f"sessions-{new}-date"] = day.isoformat()
        form[f"sessions-{new}-notes"] = "Make-up lesson"

        response = admin_client.post(
            reverse("admin:catalog_activityclass_change", args=[cls.pk]), form
        )

        assert response.status_code == 302, response.context["errors"]
        assert cls.sessions.get(date=day).is_extra
        row = family_emails(enrollment=enrollment).get()
        assert "Make-up lesson" in row.rendered_body

        # Saving the page again is not a second announcement.
        form[f"sessions-{new}-id"] = cls.sessions.get(date=day).pk
        form["sessions-INITIAL_FORMS"] = len(sessions) + 1
        admin_client.post(reverse("admin:catalog_activityclass_change", args=[cls.pk]), form)
        assert family_emails().count() == 1

    def test_the_one_row_add_screen_is_an_extra_lesson(self, admin_client):
        cls, enrollment = a_class_with_one_enrolled_child()
        day = next_saturday()

        response = admin_client.post(
            reverse("admin:catalog_classsession_add"),
            {
                "activity_class": cls.pk,
                "date": day.isoformat(),
                "start_time": "10:00",
                "end_time": "11:00",
                "location": "",
                "notes": "",
            },
        )

        assert response.status_code == 302
        session = cls.sessions.get(date=day)
        assert session.is_extra
        assert "10:00–11:00" in family_emails(enrollment=enrollment).get().rendered_body


class TestTheMcpTool:
    def test_add_extra_lesson_emails_and_reports_it(self):
        from apps.catalog import mcp_server as tools

        cls, enrollment = a_class_with_one_enrolled_child()
        day = next_saturday()

        result = tools.add_extra_lesson(
            cls.pk, day.isoformat(), "10:00", "11:00", location="Main hall", notes="Rehearsal"
        )

        assert result["added"] == {
            "date": day.isoformat(),
            "start_time": "10:00",
            "end_time": "11:00",
            "location": "Main hall",
            "notes": "Rehearsal",
        }
        assert result["children_notified"] == 1
        assert family_emails(enrollment=enrollment).exists()
        listed = {s["date"]: s for s in tools.get_class(cls.pk)["sessions"]}
        assert listed[day.isoformat()]["extra_lesson"] is True

    def test_defaults_to_the_class_time_and_place(self):
        from apps.catalog import mcp_server as tools

        cls, _ = a_class_with_one_enrolled_child()

        result = tools.add_extra_lesson(cls.pk, next_saturday().isoformat())

        assert (result["added"]["start_time"], result["added"]["location"]) == ("15:00", "Gym")

    def test_refusals_read_as_sentences(self):
        from apps.catalog import mcp_server as tools

        cls, _ = a_class_with_one_enrolled_child()
        regular = cls.sessions.filter(date__gte=timezone.localdate()).first()
        with pytest.raises(ValueError, match="already has a lesson"):
            tools.add_extra_lesson(cls.pk, regular.date.isoformat())
        with pytest.raises(ValueError, match="both"):
            tools.add_extra_lesson(cls.pk, next_saturday().isoformat(), start_time="10:00")


class TestTheTeachersPages:
    def test_the_class_page_marks_the_extra_lesson_with_its_time(self, client):
        cls, _ = a_class_with_one_enrolled_child()
        provider_user = ProviderUserFactory()
        cls.provider.members.add(provider_user)
        add_extra_lesson(
            cls, next_saturday(), start_time=datetime.time(10), end_time=datetime.time(11)
        )
        client.force_login(provider_user)

        page = client.get(reverse("provider_class", args=[cls.pk])).content.decode()

        assert "Extra lesson" in page and "10:00–11:00" in page
