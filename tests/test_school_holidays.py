"""School years, holiday periods, and the defaults they impose on sessions."""
import datetime

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import ClassSession, Holiday, generate_sessions

from .factories import (
    ActivityClassFactory,
    ChildFactory,
    HolidayFactory,
    ProviderUserFactory,
    SchoolYearFactory,
    TermFactory,
)

pytestmark = pytest.mark.django_db


def _monday(offset_weeks):
    today = timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(weeks=offset_weeks)


@pytest.fixture
def year_term_class():
    """A Monday class in a term with a school year, running 2 weeks either side."""
    year = SchoolYearFactory(
        start_date=_monday(-8), end_date=_monday(20)
    )
    term = TermFactory(
        school_year=year, start_date=_monday(-1), end_date=_monday(6)
    )
    cls = ActivityClassFactory(term=term, weekday=0)
    return year, term, cls


class TestHolidayDefaults:
    def test_sessions_skip_holiday_dates(self, year_term_class):
        year, term, cls = year_term_class
        blocked = _monday(2)
        HolidayFactory(
            school_year=year, name="Half-term", start_date=blocked,
            end_date=blocked + datetime.timedelta(days=4),
        )

        plan = generate_sessions(cls)

        dates = set(cls.sessions.values_list("date", flat=True))
        assert blocked not in dates
        assert _monday(1) in dates and _monday(3) in dates
        assert plan.skipped == 1

    def test_adding_a_holiday_removes_already_generated_sessions(self, year_term_class):
        year, term, cls = year_term_class
        generate_sessions(cls)
        blocked = _monday(3)
        assert cls.sessions.filter(date=blocked).exists()

        HolidayFactory(
            school_year=year, name="Feast day", start_date=blocked, end_date=blocked
        )

        assert not cls.sessions.filter(date=blocked).exists()

    def test_removing_a_holiday_restores_the_sessions(self, year_term_class):
        year, term, cls = year_term_class
        blocked = _monday(3)
        holiday = HolidayFactory(
            school_year=year, name="Feast day", start_date=blocked, end_date=blocked
        )
        generate_sessions(cls)
        assert not cls.sessions.filter(date=blocked).exists()

        holiday.delete()

        assert cls.sessions.filter(date=blocked).exists()

    def test_bulk_deleting_holidays_also_reconciles(self, year_term_class):
        year, term, cls = year_term_class
        blocked = _monday(3)
        HolidayFactory(
            school_year=year, name="Feast day", start_date=blocked, end_date=blocked
        )
        generate_sessions(cls)

        # The admin's "delete selected" path never calls Holiday.delete().
        Holiday.objects.filter(school_year=year).delete()

        assert cls.sessions.filter(date=blocked).exists()

    def test_holidays_of_another_school_year_do_not_apply(self, year_term_class):
        year, term, cls = year_term_class
        other = SchoolYearFactory(start_date=_monday(-8), end_date=_monday(20))
        blocked = _monday(2)
        HolidayFactory(
            school_year=other, name="Other year break", start_date=blocked, end_date=blocked
        )

        generate_sessions(cls)

        assert cls.sessions.filter(date=blocked).exists()

    def test_term_without_a_school_year_keeps_every_date(self):
        cls = ActivityClassFactory(term=TermFactory(school_year=None), weekday=0)

        plan = generate_sessions(cls)

        assert plan.skipped == 0
        assert cls.sessions.exists()

    def test_past_sessions_survive_a_retrospective_holiday(self, year_term_class):
        year, term, cls = year_term_class
        generate_sessions(cls)
        past = _monday(-1)
        assert cls.sessions.filter(date=past).exists()

        HolidayFactory(school_year=year, name="Was a holiday", start_date=past, end_date=past)

        # History is not rewritten: only future dates are reconciled away.
        assert cls.sessions.filter(date=past).exists()

    def test_sessions_with_attendance_are_never_deleted(self, year_term_class):
        from apps.enrollments.models import Attendance

        year, term, cls = year_term_class
        generate_sessions(cls)
        blocked = _monday(3)
        session = cls.sessions.get(date=blocked)
        Attendance.objects.create(session=session, child=ChildFactory(), present=True)

        HolidayFactory(school_year=year, name="Late notice", start_date=blocked, end_date=blocked)

        assert cls.sessions.filter(date=blocked).exists()


class TestOverrides:
    def test_class_flagged_to_run_through_holidays_keeps_its_dates(self, year_term_class):
        year, term, cls = year_term_class
        blocked = _monday(2)
        HolidayFactory(
            school_year=year, name="Half-term", start_date=blocked,
            end_date=blocked + datetime.timedelta(days=4),
        )
        cls.runs_during_holidays = True
        cls.save(update_fields=["runs_during_holidays"])

        plan = generate_sessions(cls)

        assert plan.skipped == 0
        assert cls.sessions.filter(date=blocked).exists()
        assert not cls.skipped_holidays().exists()

    def test_a_single_overridden_session_survives_regeneration(self, year_term_class):
        year, term, cls = year_term_class
        blocked = _monday(2)
        HolidayFactory(
            school_year=year, name="Half-term", start_date=blocked,
            end_date=blocked + datetime.timedelta(days=4),
        )
        generate_sessions(cls)
        assert not cls.sessions.filter(date=blocked).exists()

        # The office adds the one date this club does meet over the break.
        ClassSession.objects.create(
            activity_class=cls, date=blocked, holiday_override=True
        )
        generate_sessions(cls)

        assert cls.sessions.filter(date=blocked, holiday_override=True).exists()


class TestValidation:
    def test_holiday_outside_its_school_year_is_rejected(self):
        year = SchoolYearFactory(start_date=_monday(0), end_date=_monday(10))
        holiday = Holiday(
            school_year=year, name="Too late",
            start_date=_monday(11), end_date=_monday(11),
        )

        with pytest.raises(ValidationError):
            holiday.full_clean()

    def test_holiday_ending_before_it_starts_is_rejected(self):
        year = SchoolYearFactory(start_date=_monday(0), end_date=_monday(10))
        holiday = Holiday(
            school_year=year, name="Backwards",
            start_date=_monday(3), end_date=_monday(2),
        )

        with pytest.raises(ValidationError):
            holiday.full_clean()

    def test_term_outside_its_school_year_is_rejected(self):
        year = SchoolYearFactory(start_date=_monday(0), end_date=_monday(10))
        term = TermFactory.build(
            school_year=year, start_date=_monday(-2), end_date=_monday(4)
        )

        with pytest.raises(ValidationError):
            term.full_clean()


class TestSurfaces:
    def test_class_page_lists_the_holidays_it_skips(self, client, year_term_class):
        year, term, cls = year_term_class
        HolidayFactory(
            school_year=year, name="Christmas break",
            start_date=_monday(2), end_date=_monday(2) + datetime.timedelta(days=13),
        )

        response = client.get(cls.get_absolute_url())

        assert response.status_code == 200
        assert b"Christmas break" in response.content

    def test_provider_class_page_flags_the_break(self, client, year_term_class):
        year, term, cls = year_term_class
        HolidayFactory(
            school_year=year, name="Christmas break",
            start_date=_monday(2), end_date=_monday(2) + datetime.timedelta(days=13),
        )
        provider_user = ProviderUserFactory()
        cls.provider.members.add(provider_user)
        generate_sessions(cls)
        client.force_login(provider_user)

        response = client.get(reverse("provider_class", args=[cls.pk]))

        assert response.status_code == 200
        assert b"Christmas break" in response.content


class TestAdmin:
    def test_school_year_page_lists_its_holidays(self, admin_client):
        year = SchoolYearFactory(name="2026/27")
        HolidayFactory(school_year=year, name="Christmas break")

        response = admin_client.get(
            reverse("admin:catalog_schoolyear_change", args=[year.pk])
        )

        assert response.status_code == 200
        assert b"Christmas break" in response.content

    def test_copy_holidays_shifts_into_the_next_year(self, admin_client):
        source = SchoolYearFactory(start_date=_monday(0), end_date=_monday(40))
        target = SchoolYearFactory(start_date=_monday(52), end_date=_monday(92))
        HolidayFactory(
            school_year=source, name="Half-term",
            start_date=_monday(6), end_date=_monday(6) + datetime.timedelta(days=4),
        )

        response = admin_client.post(
            reverse("admin:catalog_schoolyear_changelist"),
            {
                "action": "copy_holidays",
                "_selected_action": [source.pk],
                "apply": "1",
                "target_year": target.pk,
            },
        )

        assert response.status_code == 302
        copied = target.holidays.get(name="Half-term")
        assert copied.start_date == _monday(58)
        assert copied.start_date.weekday() == 0  # still a Monday

    def test_regenerate_sessions_action_applies_current_holidays(
        self, admin_client, year_term_class
    ):
        year, term, cls = year_term_class
        generate_sessions(cls)
        blocked = _monday(3)
        # Created without firing the reconciliation signal, as a legacy row would be.
        Holiday.objects.bulk_create(
            [Holiday(school_year=year, name="Break", start_date=blocked, end_date=blocked)]
        )
        assert cls.sessions.filter(date=blocked).exists()

        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {"action": "regenerate_sessions", "_selected_action": [cls.pk]},
        )

        assert not cls.sessions.filter(date=blocked).exists()
