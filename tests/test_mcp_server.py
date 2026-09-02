"""The MCP tools Claude uses to populate the catalogue (apps.catalog.mcp_server)."""
import asyncio
import datetime
import sys

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.catalog import mcp_server as tools
from apps.catalog.models import ActivityClass, ClassSession, SchoolYear, Term
from apps.enrollments.models import Enrollment

from .factories import ActivityClassFactory, EnrollmentFactory

pytestmark = pytest.mark.django_db


def _monday(offset_weeks):
    today = timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(weeks=offset_weeks)


@pytest.fixture
def calendar():
    """A school year with a one-week holiday, and a term around it."""
    year = tools.upsert_school_year("2026/27", _monday(-8).isoformat(), _monday(30).isoformat())
    tools.upsert_holiday(
        "2026/27", "Half term", _monday(2).isoformat(), (_monday(2) + datetime.timedelta(days=6)).isoformat()
    )
    term = tools.upsert_term(
        "Autumn", _monday(-1).isoformat(), _monday(6).isoformat(), school_year="2026/27", is_active=True
    )
    tools.upsert_provider("Chess Club Ltd", contact_email="hi@chess.test")
    return year, term


def _chess(**overrides):
    args = dict(
        term="Autumn",
        provider="Chess Club Ltd",
        title="Chess Club",
        description="Learn to play.",
        age_min=6,
        age_max=11,
        weekday=1,
        start_time="15:30",
        end_time="16:30",
    )
    args.update(overrides)
    return tools.upsert_class(**args)


# --- calendar --------------------------------------------------------------- #


def test_upsert_school_year_is_idempotent():
    first = tools.upsert_school_year("2026/27", "2026-09-01", "2027-06-30")
    second = tools.upsert_school_year("2026/27", "2026-09-07", "2027-07-02")
    assert first["created"] is True
    assert second["created"] is False
    assert SchoolYear.objects.count() == 1
    assert SchoolYear.objects.get().start_date == datetime.date(2026, 9, 7)


def test_bad_date_and_out_of_range_holiday_are_readable_errors(calendar):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.upsert_school_year("x", "1 Sept", "2027-06-30")
    with pytest.raises(ValueError, match="falls outside it"):
        tools.upsert_holiday("2026/27", "Too late", "2028-01-01", "2028-01-02")
    with pytest.raises(ValueError, match="Known school years: 2026/27"):
        tools.upsert_term("Spring", "2027-01-05", "2027-03-30", school_year="1999/00")


def test_upsert_term_keeps_active_flag_unless_told(calendar):
    _, term = calendar
    assert term["is_active"] is True
    again = tools.upsert_term("Autumn", _monday(-1).isoformat(), _monday(7).isoformat(), school_year="2026/27")
    assert again["created"] is False
    assert again["is_active"] is True
    assert Term.objects.count() == 1


# --- classes ---------------------------------------------------------------- #


def test_upsert_class_creates_draft_then_updates_in_place(calendar):
    created = _chess()
    assert created["created"] is True
    assert created["status"] == ActivityClass.Status.DRAFT
    assert created["slug"] == "chess-club"
    assert created["session_count"] == 0

    updated = _chess(capacity=20, location="Library")
    assert updated["created"] is False
    assert updated["capacity"] == 20
    assert ActivityClass.objects.count() == 1

    other = _chess(slug="chess-club-beginners")
    assert other["created"] is True
    assert ActivityClass.objects.count() == 2


def test_upsert_class_validation_errors(calendar):
    with pytest.raises(ValueError, match="end_time must be after start_time"):
        _chess(end_time="15:00")
    with pytest.raises(ValueError, match="age_max"):
        _chess(age_max=3)
    with pytest.raises(ValueError, match="Known providers: Chess Club Ltd"):
        _chess(provider="Nobody")
    with pytest.raises(ValueError, match="HH:MM"):
        _chess(start_time="half three")


def test_publish_generates_sessions_and_skips_holidays(calendar):
    cls_id = _chess()["id"]
    result = tools.publish_class(cls_id)
    assert result["status"] == ActivityClass.Status.PUBLISHED
    assert "skipped for school holidays" in result["sessions"]
    dates = list(ClassSession.objects.filter(activity_class_id=cls_id).values_list("date", flat=True))
    assert dates, "sessions were generated"
    assert all(d.weekday() == 1 for d in dates)
    holiday_week = {_monday(2) + datetime.timedelta(days=i) for i in range(7)}
    assert not holiday_week & set(dates)
    assert result["session_count"] == len(dates)

    # Re-upserting a published class with a new weekday moves its calendar
    # (past sessions are kept as history, exactly as the admin action does).
    moved = _chess(weekday=3)
    assert moved["sessions"] is not None
    future = ClassSession.objects.filter(
        activity_class_id=cls_id, date__gte=timezone.localdate()
    ).values_list("date", flat=True)
    assert future and all(d.weekday() == 3 for d in future)


def test_get_class_and_list_classes_expose_counts_not_families():
    cls = ActivityClassFactory(capacity=3)
    EnrollmentFactory(activity_class=cls, status=Enrollment.Status.ENROLLED)
    EnrollmentFactory(activity_class=cls, status=Enrollment.Status.WAITLISTED)
    EnrollmentFactory(activity_class=cls, status=Enrollment.Status.REQUESTED)

    listed = tools.list_classes(term=cls.term.name, status="published")
    assert [c["id"] for c in listed] == [cls.id]
    row = listed[0]
    assert (row["enrolled_count"], row["waitlist_count"], row["requested_count"], row["places_free"]) == (1, 1, 1, 2)

    detail = tools.get_class(cls.id)
    assert detail["description"] == cls.description
    flat = repr(detail)
    for child in (e.child for e in Enrollment.objects.select_related("child")):
        assert child.first_name not in flat
        assert child.last_name not in flat

    with pytest.raises(ValueError, match="Unknown status"):
        tools.list_classes(status="open")
    with pytest.raises(ValueError, match="No class with id"):
        tools.get_class(999)


def test_get_overview_lists_calendar_and_counts(calendar):
    _chess()
    overview = tools.get_overview()
    assert overview["school_years"][0]["holidays"][0]["name"] == "Half term"
    assert overview["terms"][0]["name"] == "Autumn"
    assert overview["providers"][0]["name"] == "Chess Club Ltd"
    assert overview["class_counts_by_status"]["DRAFT"] == 1


def test_archive_refuses_active_enrolments_then_succeeds():
    cls = ActivityClassFactory()
    enrollment = EnrollmentFactory(activity_class=cls, status=Enrollment.Status.ENROLLED)
    with pytest.raises(ValueError, match="active enrolment"):
        tools.archive_class(cls.id)
    enrollment.status = Enrollment.Status.CANCELLED
    enrollment.save()
    assert tools.archive_class(cls.id)["status"] == ActivityClass.Status.ARCHIVED
    with pytest.raises(ValueError, match="cannot be edited"):
        tools.upsert_class(
            term=cls.term.name, provider=cls.provider.name, title=cls.title, slug=cls.slug,
            description="x", age_min=5, age_max=12, weekday=0, start_time="15:00", end_time="16:00",
        )


def test_cancel_class_goes_through_the_service(default_notification_templates):
    cls = ActivityClassFactory()
    enrollment = EnrollmentFactory(activity_class=cls, status=Enrollment.Status.ENROLLED)
    result = tools.cancel_class(cls.id)
    assert result["status"] == ActivityClass.Status.CANCELLED
    enrollment.refresh_from_db()
    assert enrollment.status == Enrollment.Status.CANCELLED
    assert enrollment.cancel_reason == Enrollment.CancelReason.CLASS_CANCELLED
    with pytest.raises(ValueError, match="cannot be published"):
        tools.publish_class(cls.id)


# --- server wiring ---------------------------------------------------------- #


def test_server_registers_every_tool():
    pytest.importorskip("mcp")
    server = tools.build_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {fn.__name__ for fn in tools.TOOLS}
    schema = next(t for t in asyncio.run(server.list_tools()) if t.name == "upsert_class").inputSchema
    assert set(schema["required"]) == {
        "term", "provider", "title", "description", "age_min", "age_max", "weekday", "start_time", "end_time",
    }


def test_command_explains_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(CommandError, match="pip install"):
        call_command("mcp_server")
