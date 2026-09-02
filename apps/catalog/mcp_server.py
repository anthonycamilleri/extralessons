"""MCP tools that let an AI assistant read and populate the catalogue.

Served over stdio by ``manage.py mcp_server`` and consumed by Claude Code or
Claude Desktop. The functions here are plain, synchronous Python so they can
be unit-tested directly and reused by any other transport later.

Scope is deliberately narrow: the school calendar and the class catalogue can
be read and written; enrolment figures are read-only aggregates; families,
children and individual enrolments are never exposed. Every write goes through
the same model validation and service functions the Django admin uses.

Conventions for callers: dates are ISO ``YYYY-MM-DD``, times ``HH:MM``, weekdays
``0`` (Monday) to ``6`` (Sunday). Related records are addressed by name (school
year, term, provider); classes by the numeric ``id`` returned by ``list_classes``.
"""
from __future__ import annotations

import datetime
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils.text import slugify

from apps.accounts.models import SiteConfig
from apps.catalog.models import (
    ActivityClass,
    ClassSession,
    Holiday,
    Provider,
    SchoolYear,
    Term,
    generate_sessions,
)
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.enrollments.services import cancel_class as _cancel_class_service

SERVER_NAME = "extralessons"
SERVER_INSTRUCTIONS = (
    "Extralessons is a school's after-school club booking system. Start with "
    "get_overview to see the school years, terms and providers that already "
    "exist, then list_classes for the catalogue. Upsert tools are idempotent: "
    "calling them again with the same name updates the record instead of "
    "duplicating it. New classes are created as DRAFT and only appear to "
    "parents after publish_class. Editing a class never changes its lesson "
    "dates; use cancel_sessions for individual dates that are off, and "
    "regenerate_sessions only when the whole calendar should follow a new "
    "schedule. Ask the user before cancel_class: it cancels every family's "
    "place and notifies them."
)


# --------------------------------------------------------------------------- #
# Error translation
# --------------------------------------------------------------------------- #


def _flatten_validation(error):
    if hasattr(error, "message_dict"):
        parts = []
        for field, messages in error.message_dict.items():
            prefix = "" if field == "__all__" else f"{field}: "
            parts.extend(prefix + m for m in messages)
        return " ".join(parts)
    return " ".join(error.messages)


def tool_errors(fn):
    """Turn model/service failures into a readable ValueError.

    FastMCP reports a raised exception back to the assistant as an error
    result, so a clear sentence here is what lets it fix its input and retry.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():
                return fn(*args, **kwargs)
        except ValidationError as e:
            raise ValueError(_flatten_validation(e)) from e
        except EnrollmentError as e:
            raise ValueError(str(e)) from e
        except ObjectDoesNotExist as e:
            raise ValueError(str(e) or "Record not found.") from e
        except IntegrityError as e:
            raise ValueError(f"Database constraint violated: {e}") from e

    return wrapper


# --------------------------------------------------------------------------- #
# Parsing and lookup helpers
# --------------------------------------------------------------------------- #


def _date(value, label):
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{label} must be a date in YYYY-MM-DD format, got {value!r}.")


def _time(value, label):
    if isinstance(value, datetime.time):
        return value
    try:
        return datetime.time.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{label} must be a time in HH:MM format, got {value!r}.")


def _school_year(name):
    try:
        return SchoolYear.objects.get(name=name)
    except SchoolYear.DoesNotExist:
        known = ", ".join(SchoolYear.objects.values_list("name", flat=True)) or "none yet"
        raise ValueError(f"No school year named {name!r}. Known school years: {known}.")


def _term(name, school_year=None):
    qs = Term.objects.filter(name=name)
    if school_year:
        qs = qs.filter(school_year__name=school_year)
    terms = list(qs.select_related("school_year"))
    if len(terms) == 1:
        return terms[0]
    if not terms:
        known = ", ".join(str(t) for t in Term.objects.all()) or "none yet"
        raise ValueError(f"No term named {name!r}. Known terms: {known}.")
    years = ", ".join(str(t.school_year) for t in terms)
    raise ValueError(
        f"Several terms are named {name!r} (school years: {years}); pass school_year too."
    )


def _provider(name):
    try:
        return Provider.objects.get(name=name)
    except Provider.DoesNotExist:
        known = ", ".join(Provider.objects.values_list("name", flat=True)) or "none yet"
        raise ValueError(f"No provider named {name!r}. Known providers: {known}.")


def _annotated_classes():
    """Classes with seat counts and a session count, ready for _class_dict.

    The session count is a subquery rather than a second Count(): joining
    sessions onto the enrolment counts in with_counts() would multiply them.
    """
    sessions = (
        ClassSession.objects.filter(activity_class=OuterRef("pk"))
        .order_by()
        .values("activity_class")
        .annotate(n=Count("id"))
        .values("n")
    )
    return (
        ActivityClass.objects.with_counts()
        .annotate(session_count=Coalesce(Subquery(sessions, output_field=IntegerField()), 0))
        .select_related("term", "term__school_year", "provider")
    )


def _class(class_id):
    try:
        return _annotated_classes().get(pk=class_id)
    except ActivityClass.DoesNotExist:
        raise ValueError(f"No class with id {class_id}. Use list_classes to find ids.")


def _save(instance, **kwargs):
    instance.full_clean()
    instance.save(**kwargs)
    return instance


# --------------------------------------------------------------------------- #
# Serialisers (dicts only, so results are JSON as-is)
# --------------------------------------------------------------------------- #


def _school_year_dict(year):
    return {
        "name": year.name,
        "start_date": year.start_date.isoformat(),
        "end_date": year.end_date.isoformat(),
        "holidays": [
            {
                "name": h.name,
                "start_date": h.start_date.isoformat(),
                "end_date": h.end_date.isoformat(),
            }
            for h in year.holidays.all()
        ],
    }


def _term_dict(term):
    return {
        "id": term.id,
        "name": term.name,
        "school_year": term.school_year.name if term.school_year_id else None,
        "start_date": term.start_date.isoformat(),
        "end_date": term.end_date.isoformat(),
        "is_active": term.is_active,
    }


def _provider_dict(provider):
    return {
        "id": provider.id,
        "name": provider.name,
        "description": provider.description,
        "contact_email": provider.contact_email,
        "contact_phone": provider.contact_phone,
    }


def _class_dict(cls, counts=True):
    data = {
        "id": cls.id,
        "title": cls.title,
        "slug": cls.slug,
        "status": cls.status,
        "term": cls.term.name,
        "school_year": cls.term.school_year.name if cls.term.school_year_id else None,
        "provider": cls.provider.name,
        "weekday": cls.weekday,
        "weekday_name": cls.get_weekday_display(),
        "start_time": cls.start_time.strftime("%H:%M"),
        "end_time": cls.end_time.strftime("%H:%M"),
        "location": cls.location,
        "age_min": cls.age_min,
        "age_max": cls.age_max,
        "capacity": cls.capacity,
        "runs_during_holidays": cls.runs_during_holidays,
        "public_url_path": cls.get_absolute_url(),
    }
    if counts:
        # Annotated by _annotated_classes().
        data.update(
            enrolled_count=cls.enrolled_count,
            waitlist_count=cls.waitlist_count,
            requested_count=cls.requested_count,
            places_free=cls.places_free,
            session_count=cls.session_count,
        )
    return data


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #


@tool_errors
def get_overview() -> dict:
    """Snapshot of the school: name, school years with holidays, terms, providers, class counts.

    Call this first to learn what already exists before creating anything.
    """
    config = SiteConfig.get()
    by_status = dict(
        ActivityClass.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )
    return {
        "school_name": config.school_name,
        "contact_email": config.contact_email,
        "signup_open": config.signup_open,
        "offer_ttl_hours": config.offer_ttl_hours,
        "school_years": [
            _school_year_dict(y) for y in SchoolYear.objects.prefetch_related("holidays")
        ],
        "terms": [_term_dict(t) for t in Term.objects.select_related("school_year")],
        "providers": [_provider_dict(p) for p in Provider.objects.all()],
        "class_counts_by_status": {
            status: by_status.get(status, 0) for status, _ in ActivityClass.Status.choices
        },
    }


@tool_errors
def list_classes(term: str | None = None, status: str | None = None) -> list[dict]:
    """List classes with seat counts. Filter by term name and/or status.

    Status is one of DRAFT, PUBLISHED, CANCELLED, ARCHIVED. Returns the numeric
    id used by get_class, publish_class, archive_class and cancel_class.
    """
    qs = _annotated_classes()
    if term:
        qs = qs.filter(term__name=term)
    if status:
        status = status.upper()
        if status not in ActivityClass.Status.values:
            raise ValueError(
                f"Unknown status {status!r}; use one of {', '.join(ActivityClass.Status.values)}."
            )
        qs = qs.filter(status=status)
    return [_class_dict(c) for c in qs]


@tool_errors
def get_class(class_id: int) -> dict:
    """Full detail of one class: description, seat counts, session dates and skipped holidays."""
    cls = _class(class_id)
    data = _class_dict(cls)
    data.update(
        description=cls.description,
        extra_details=cls.extra_details,
        has_image=bool(cls.image),
        sessions=[
            {
                "date": s.date.isoformat(),
                "cancelled": s.cancelled,
                "runs_despite_holiday": s.holiday_override,
                "notes": s.notes,
            }
            for s in cls.sessions.all()
        ],
        skipped_holidays=[
            {"name": h.name, "start_date": h.start_date.isoformat(), "end_date": h.end_date.isoformat()}
            for h in cls.skipped_holidays()
        ],
    )
    return data


# --------------------------------------------------------------------------- #
# Write tools: school calendar
# --------------------------------------------------------------------------- #


@tool_errors
def upsert_school_year(name: str, start_date: str, end_date: str) -> dict:
    """Create or update a school year by name (for example "2026/27").

    Terms and holidays hang off a school year, so create it first.
    """
    year, created = SchoolYear.objects.get_or_create(
        name=name,
        defaults={"start_date": _date(start_date, "start_date"), "end_date": _date(end_date, "end_date")},
    )
    if not created:
        year.start_date = _date(start_date, "start_date")
        year.end_date = _date(end_date, "end_date")
    _save(year)
    return {"created": created, **_school_year_dict(year)}


@tool_errors
def upsert_holiday(school_year: str, name: str, start_date: str, end_date: str) -> dict:
    """Create or update a school holiday (half-term, Christmas, a public holiday).

    Dates are inclusive; for a single day repeat the start date. Every class in
    the year skips these dates automatically, and existing session calendars
    are re-reconciled straight away.
    """
    year = _school_year(school_year)
    start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
    holiday, created = Holiday.objects.get_or_create(
        school_year=year, name=name, defaults={"start_date": start, "end_date": end}
    )
    if not created:
        holiday.start_date, holiday.end_date = start, end
    _save(holiday)
    return {
        "created": created,
        "school_year": year.name,
        "name": holiday.name,
        "start_date": holiday.start_date.isoformat(),
        "end_date": holiday.end_date.isoformat(),
    }


@tool_errors
def upsert_term(
    name: str,
    start_date: str,
    end_date: str,
    school_year: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """Create or update a term (for example "Autumn 2026") within a school year.

    Only active terms are shown in the public catalogue; pass is_active to
    change the flag, leave it out to keep the current value (new terms
    default to inactive).
    """
    year = _school_year(school_year) if school_year else None
    term = Term.objects.filter(name=name, school_year=year).first()
    created = term is None
    if created:
        term = Term(name=name, school_year=year)
    term.start_date = _date(start_date, "start_date")
    term.end_date = _date(end_date, "end_date")
    if is_active is not None:
        term.is_active = is_active
    _save(term)
    return {"created": created, **_term_dict(term)}


# --------------------------------------------------------------------------- #
# Write tools: providers and classes
# --------------------------------------------------------------------------- #


@tool_errors
def upsert_provider(
    name: str, description: str = "", contact_email: str = "", contact_phone: str = ""
) -> dict:
    """Create or update a provider (the organisation or coach running classes) by name.

    Linking provider-role user accounts to it is done by the school in the
    Django admin, not here.
    """
    provider, created = Provider.objects.get_or_create(name=name)
    provider.description = description
    provider.contact_email = contact_email
    provider.contact_phone = contact_phone
    _save(provider)
    return {"created": created, **_provider_dict(provider)}


@tool_errors
def upsert_class(
    term: str,
    provider: str,
    title: str,
    description: str,
    age_min: int,
    age_max: int,
    weekday: int,
    start_time: str,
    end_time: str,
    capacity: int = 15,
    location: str = "",
    extra_details: str = "",
    runs_during_holidays: bool = False,
    slug: str | None = None,
    school_year: str | None = None,
    rebuild_sessions: bool = False,
) -> dict:
    """Create or update a class in a term. Matched by slug (derived from the title) within the term.

    New classes start as DRAFT; call publish_class to open them to parents and
    generate their session calendar. Updating an existing class never touches
    its session dates unless rebuild_sessions is true: cancelled lessons and
    one-off overrides survive edits to the title, description, capacity and so
    on. If the schedule itself changed (term, weekday, times, holiday rule)
    the result says so; call regenerate_sessions, or pass rebuild_sessions,
    when the dates should follow. Pass an explicit slug to keep two classes
    with the same title apart. Set runs_during_holidays for holiday camps.
    Cancelled or archived classes cannot be edited.
    """
    term_obj = _term(term, school_year)
    provider_obj = _provider(provider)
    slug = slugify(slug or title)
    if not slug:
        raise ValueError("The title must contain at least one letter or digit.")

    cls = ActivityClass.objects.filter(term=term_obj, slug=slug).first()
    created = cls is None
    if created:
        cls = ActivityClass(term=term_obj, slug=slug)
    elif cls.status in (ActivityClass.Status.CANCELLED, ActivityClass.Status.ARCHIVED):
        raise ValueError(
            f"Class {cls.id} ({cls.title}) is {cls.get_status_display().lower()} and cannot be "
            "edited. Create it under a different slug instead."
        )

    start, end = _time(start_time, "start_time"), _time(end_time, "end_time")
    # Say these in words: the model enforces them as database constraints,
    # whose failure message only names the constraint.
    if end <= start:
        raise ValueError("end_time must be after start_time.")
    if age_max < age_min:
        raise ValueError("age_max must be at least age_min.")
    if capacity < 1:
        raise ValueError("capacity must be at least 1.")

    # The fields the session calendar is derived from. Compared before the
    # save so the caller can be told when the dates no longer match.
    schedule_before = (cls.term_id, cls.weekday, cls.start_time, cls.end_time, cls.runs_during_holidays)

    cls.provider = provider_obj
    cls.title = title
    cls.description = description
    cls.extra_details = extra_details
    cls.age_min = age_min
    cls.age_max = age_max
    cls.capacity = capacity
    cls.weekday = weekday
    cls.start_time = start
    cls.end_time = end
    cls.location = location
    cls.runs_during_holidays = runs_during_holidays
    _save(cls)

    schedule_after = (cls.term_id, cls.weekday, cls.start_time, cls.end_time, cls.runs_during_holidays)
    schedule_changed = not created and schedule_before != schedule_after
    has_sessions = not created and cls.sessions.exists()

    sessions = None
    note = None
    if has_sessions and rebuild_sessions:
        sessions = generate_sessions(cls).summary
    elif has_sessions and schedule_changed:
        note = (
            "The schedule changed but the existing session dates were left as they "
            "are. Call regenerate_sessions to rebuild them (cancelled lessons stay "
            "cancelled), or upsert again with rebuild_sessions=true."
        )
    cls = _class(cls.id)
    result = {"created": created, "sessions": sessions, "schedule_changed": schedule_changed, **_class_dict(cls)}
    if note:
        result["note"] = note
    return result


@tool_errors
def publish_class(class_id: int) -> dict:
    """Publish a class so parents can request places, and generate its session dates.

    Sessions follow the term dates and the class weekday, skipping school
    holidays. Publishing an already published class just re-reconciles the
    sessions. Cancelled classes cannot be published.
    """
    cls = _class(class_id)
    if cls.status == ActivityClass.Status.CANCELLED:
        raise ValueError(f"Class {cls.id} ({cls.title}) is cancelled and cannot be published.")
    cls.status = ActivityClass.Status.PUBLISHED
    cls.save(update_fields=["status"])
    plan = generate_sessions(cls)
    return {"sessions": plan.summary, **_class_dict(_class(cls.id))}


@tool_errors
def regenerate_sessions(class_id: int) -> dict:
    """Re-reconcile a class's session dates with its schedule and the school holidays.

    Safe to repeat: existing dates are kept (cancelled ones stay cancelled),
    missing ones added, and future dates that no longer fit are removed unless
    attendance was taken.
    """
    cls = _class(class_id)
    plan = generate_sessions(cls)
    return {"sessions": plan.summary, **_class_dict(_class(cls.id))}


def _sessions_on(cls, dates, label):
    """The class's sessions on the given ISO dates, or a ValueError naming the misses."""
    wanted = [_date(value, label) for value in dates]
    if not wanted:
        raise ValueError(f"{label} must list at least one date.")
    found = {s.date: s for s in cls.sessions.filter(date__in=wanted)}
    missing = [d.isoformat() for d in wanted if d not in found]
    if missing:
        have = ", ".join(d.isoformat() for d in cls.sessions.values_list("date", flat=True)[:60])
        raise ValueError(
            f"Class {cls.id} ({cls.title}) has no session on {', '.join(missing)}. "
            f"Its dates are: {have or 'none yet — publish it first'}."
        )
    return [found[d] for d in wanted]


@tool_errors
def cancel_sessions(class_id: int, dates: list[str], notes: str = "") -> dict:
    """Cancel individual lessons of a class by date (YYYY-MM-DD), for example a date the provider cannot make.

    The dates stay in the calendar marked as cancelled, so providers and
    parents see that the lesson is off and regenerate_sessions will not bring
    them back. Optional notes are shown against each cancelled date. Lessons
    whose attendance has already been recorded cannot be cancelled. Use
    restore_sessions to undo.
    """
    cls = _class(class_id)
    sessions = _sessions_on(cls, dates, "dates")
    taken = [s.date.isoformat() for s in sessions if s.attendance.exists()]
    if taken:
        raise ValueError(
            f"Attendance has already been recorded for {', '.join(taken)}; those lessons "
            "happened and cannot be cancelled."
        )
    with transaction.atomic():
        for session in sessions:
            session.cancelled = True
            if notes:
                session.notes = notes[:200]
            session.save(update_fields=["cancelled", "notes"])
    return {
        "cancelled": [s.date.isoformat() for s in sessions],
        "cancelled_total": cls.sessions.filter(cancelled=True).count(),
        **_class_dict(_class(cls.id)),
    }


@tool_errors
def restore_sessions(class_id: int, dates: list[str]) -> dict:
    """Undo cancel_sessions: the lessons on these dates (YYYY-MM-DD) are on again."""
    cls = _class(class_id)
    sessions = _sessions_on(cls, dates, "dates")
    with transaction.atomic():
        for session in sessions:
            session.cancelled = False
            session.notes = ""
            session.save(update_fields=["cancelled", "notes"])
    return {
        "restored": [s.date.isoformat() for s in sessions],
        "cancelled_total": cls.sessions.filter(cancelled=True).count(),
        **_class_dict(_class(cls.id)),
    }


@tool_errors
def delete_term(name: str, school_year: str | None = None) -> dict:
    """Delete a term that has no classes, for example one created by mistake.

    Refused while any class, in any status, belongs to it: move or archive
    those first. Deleting the active term leaves the school with no active
    term until upsert_term sets another.
    """
    term = _term(name, school_year)
    classes = ActivityClass.objects.filter(term=term)
    if classes.exists():
        titles = ", ".join(classes.order_by("title").values_list("title", flat=True)[:10])
        raise ValueError(
            f"Term {term.name!r} still has {classes.count()} class(es) ({titles}); "
            "a term with classes cannot be deleted."
        )
    data = _term_dict(term)
    term.delete()
    return {"deleted": True, **data}


@tool_errors
def archive_class(class_id: int) -> dict:
    """Archive a class that has finished. Refused while it still has active enrolments."""
    cls = _class(class_id)
    active = cls.enrollments.filter(status__in=Enrollment.ACTIVE_STATUSES).count()
    if active:
        raise ValueError(
            f"Class {cls.id} ({cls.title}) still has {active} active enrolment(s); "
            "cancel the class first if it is not running."
        )
    cls.status = ActivityClass.Status.ARCHIVED
    cls.save(update_fields=["status"])
    return _class_dict(_class(cls.id))


@tool_errors
def cancel_class(class_id: int) -> dict:
    """Cancel a class: every family's place is cancelled and they are notified. Confirm with the user first.

    This cannot be undone from here. Use archive_class for a class that simply
    ended.
    """
    cls = _class(class_id)
    _cancel_class_service(cls)
    return _class_dict(_class(cls.id))


TOOLS = [
    get_overview,
    list_classes,
    get_class,
    upsert_school_year,
    upsert_holiday,
    upsert_term,
    upsert_provider,
    upsert_class,
    publish_class,
    regenerate_sessions,
    cancel_sessions,
    restore_sessions,
    delete_term,
    archive_class,
    cancel_class,
]


def build_server():
    """Assemble the FastMCP server. Imported lazily so the ``mcp`` extra is optional."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(fn)
    return server
