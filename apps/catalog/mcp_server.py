"""MCP tools that let an AI assistant read and populate the catalogue.

Served over stdio by ``manage.py mcp_server`` and consumed by Claude Code or
Claude Desktop. The functions here are plain, synchronous Python so they can
be unit-tested directly and reused by any other transport later.

Scope: the school calendar and the class catalogue can be read and written,
and the registrations behind the figures can be read. Nothing about a
registration can be written from here — places are approved, offered and
cancelled in the office, not by an assistant — and the family's own login,
passwords and messages are never exposed. Every write goes through the same
model validation and service functions the Django admin uses.

The registration tools return children's names, and on request their
guardians' contact details and the notes the school keeps for providers. That
is personal data about children: the token that reaches these tools carries
the trust of a school-office login, and results should go no further than the
question that was asked.

Conventions for callers: dates are ISO ``YYYY-MM-DD``, times ``HH:MM``, weekdays
``0`` (Monday) to ``6`` (Sunday). Related records are addressed by name (school
year, term, provider); classes by the numeric ``id`` returned by ``list_classes``.
"""
from __future__ import annotations

import datetime
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
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
from apps.enrollments.services import notify_lessons_cancelled

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
    "place and notifies them. Who is registered for what is read-only: "
    "list_registrations across classes, get_class_register for one class's "
    "register. Those return children's personal data, so ask for contact "
    "details or care notes only when the task needs them, and keep the "
    "results to the question at hand."
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
            # Seats held (confirmed + live offers) and what that leaves to fill.
            enrolled_count=cls.enrolled_count,
            confirmed_count=cls.confirmed_count,
            offered_count=cls.offered_count,
            waitlist_count=cls.waitlist_count,
            requested_count=cls.requested_count,
            places_free=cls.places_free,
            # Every live registration, and what parents see as still available.
            registrations_count=cls.registrations_count,
            places_available=cls.places_available,
            session_count=cls.session_count,
        )
    return data


def _moment(value):
    """A timestamp as a local ISO string to the minute, or None."""
    return timezone.localtime(value).isoformat(timespec="minutes") if value else None


def _guardian_dict(user):
    return {
        "name": user.get_full_name(),
        "email": user.email,
        "phone": user.phone_e164,
    }


def _registration_dict(enrollment, *, contacts=False, care_notes=False, with_class=True, position=None):
    """One child's place in one class. Personal data: see the tool docstrings.

    Only the timestamps that mean something for the current status are
    included, so a row stays short enough to read at a glance.
    """
    child = enrollment.child
    data = {
        "enrollment_id": enrollment.id,
        "status": enrollment.status,
        "status_label": enrollment.get_status_display(),
        "child_id": child.id,
        "child_name": child.full_name,
        "school_class": child.school_class,
        "registered_at": _moment(enrollment.created_at),
        # The child's age at term start when it sits outside the class's
        # recommended range (the parent confirmed a warning), else None.
        "age_outside_range": enrollment.age_outside_range,
        "cancellation_requested": enrollment.cancellation_requested,
    }
    if with_class:
        cls = enrollment.activity_class
        data["class"] = {
            "id": cls.id,
            "title": cls.title,
            "status": cls.status,
            "term": cls.term.name,
            "school_year": cls.term.school_year.name if cls.term.school_year_id else None,
            "provider": cls.provider.name,
            "weekday_name": cls.get_weekday_display(),
            "start_time": cls.start_time.strftime("%H:%M"),
            "end_time": cls.end_time.strftime("%H:%M"),
        }
    status = enrollment.status
    if status == Enrollment.Status.ENROLLED:
        data["enrolled_at"] = _moment(enrollment.enrolled_at)
        data["from_waiting_list"] = enrollment.promoted_from_waitlist
    elif status == Enrollment.Status.WAITLISTED:
        data["waitlisted_at"] = _moment(enrollment.waitlisted_at)
        data["waitlist_position"] = position
    elif status == Enrollment.Status.OFFERED:
        data["offered_at"] = _moment(enrollment.offered_at)
        data["offer_expires_at"] = _moment(enrollment.offer_expires_at)
        data["offer_expired"] = bool(
            enrollment.offer_expires_at and enrollment.offer_expires_at < timezone.now()
        )
    elif status == Enrollment.Status.CANCELLED:
        data["cancelled_at"] = _moment(enrollment.cancelled_at)
        data["cancel_reason"] = enrollment.get_cancel_reason_display() or None
    if care_notes:
        data["may_leave_alone"] = child.may_leave_alone
        data["care_notes"] = child.notes
    if contacts:
        data["date_of_birth"] = child.date_of_birth.isoformat()
        data["guardians"] = [_guardian_dict(g) for g in child.guardians.all()]
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
# Read tools: registrations (personal data — see the module docstring)
# --------------------------------------------------------------------------- #


def _registrations(contacts=False):
    """Base queryset for the registration tools, joined for the serialiser."""
    qs = Enrollment.objects.select_related(
        "child",
        "activity_class",
        "activity_class__term",
        "activity_class__term__school_year",
        "activity_class__provider",
    )
    if contacts:
        qs = qs.prefetch_related("child__guardians")
    return qs


def _statuses(status, include_cancelled):
    """The enrolment statuses a registration query should return."""
    if status:
        wanted = status.upper()
        if wanted == "ACTIVE":
            return list(Enrollment.ACTIVE_STATUSES)
        if wanted not in Enrollment.Status.values:
            known = ", ".join(Enrollment.Status.values)
            raise ValueError(f"Unknown status {status!r}; use ACTIVE or one of {known}.")
        return [wanted]
    statuses = list(Enrollment.ACTIVE_STATUSES)
    if include_cancelled:
        statuses.append(Enrollment.Status.CANCELLED)
    return statuses


def _waitlist_positions(class_ids):
    """enrolment id -> 1-based place in the queue, for whole classes at a time.

    Enrollment.waitlist_position() answers for one row with one query, which a
    list of a hundred registrations cannot afford; this walks the same FIFO
    order (waitlisted_at, id) once for every class in the result.
    """
    positions = {}
    counter = {}
    rows = (
        Enrollment.objects.filter(activity_class_id__in=list(class_ids))
        .waitlist_fifo()
        .values_list("id", "activity_class_id")
    )
    for enrollment_id, class_id in rows:
        counter[class_id] = counter.get(class_id, 0) + 1
        positions[enrollment_id] = counter[class_id]
    return positions


@tool_errors
def list_registrations(
    class_id: int | None = None,
    term: str | None = None,
    child: str | None = None,
    school_class: str | None = None,
    status: str | None = None,
    include_cancelled: bool = False,
    include_contacts: bool = False,
    limit: int = 200,
) -> dict:
    """Who is registered for what: one row per child per class, with the status of their place.

    This returns children's names, so treat the result as school office
    paperwork: use it to answer the question that was asked and do not copy it
    anywhere else. Contact details are left out unless include_contacts is
    true, which adds each child's date of birth and their guardians' names,
    email addresses and phone numbers.

    Filters combine (all optional): class_id for one class, term by name,
    school_class for a school class code such as "P3E", child for part of a
    child's name, status for one of REQUESTED, ENROLLED, WAITLISTED, OFFERED,
    CANCELLED, or ACTIVE for every live one. Cancelled places are left out by
    default; pass include_cancelled to see them alongside the live ones.

    Rows carry the queue position for waitlisted children, the expiry for
    outstanding offers, and cancellation_requested for a family that has asked
    to give up a confirmed place. Use get_class_register for the register of a
    single class, grouped and in the right order.
    """
    qs = _registrations(include_contacts).filter(status__in=_statuses(status, include_cancelled))
    if class_id is not None:
        qs = qs.filter(activity_class=_class(class_id))
    if term:
        qs = qs.filter(activity_class__term__name=term)
    if school_class:
        qs = qs.filter(child__school_class__iexact=school_class)
    if child:
        # Each word has to match a name, so "Lena Parent" finds that child and
        # a bare "parent" everyone in the family.
        for word in child.split():
            qs = qs.filter(
                Q(child__first_name__icontains=word) | Q(child__last_name__icontains=word)
            )
    qs = qs.order_by("activity_class__title", "child__first_name", "child__last_name")

    limit = max(1, min(int(limit), 1000))
    matched = qs.count()
    rows = list(qs[:limit])
    positions = _waitlist_positions({r.activity_class_id for r in rows})
    result = {
        "matched": matched,
        "returned": len(rows),
        "truncated": matched > len(rows),
        "registrations": [
            _registration_dict(r, contacts=include_contacts, position=positions.get(r.id))
            for r in rows
        ],
    }
    if result["truncated"]:
        result["note"] = (
            f"Only the first {len(rows)} of {matched} registrations are shown; narrow the "
            "filters or raise limit."
        )
    return result


@tool_errors
def get_class_register(
    class_id: int, include_contacts: bool = False, include_care_notes: bool = False
) -> dict:
    """The register for one class: who holds a place, who has an offer, who is waiting, who is asking.

    The class with its seat counts, plus four lists of children: enrolled (the
    register the provider takes), offered (a waiting-list place offered and not
    yet answered), waitlisted (in queue order) and requested (awaiting the
    office's review, oldest first). Cancelled places are not listed; ask
    list_registrations with include_cancelled for those.

    Personal data, as list_registrations. include_contacts adds guardians and
    dates of birth; include_care_notes adds what the school records for the
    provider — whether the child may go home unaccompanied, and the family's
    notes, which routinely mention allergies and medical needs. Leave both off
    unless the answer needs them.
    """
    cls = _class(class_id)
    rows = list(
        _registrations(include_contacts).filter(
            activity_class=cls, status__in=Enrollment.ACTIVE_STATUSES
        )
    )
    positions = _waitlist_positions([cls.id])

    def dicts(status, key):
        chosen = [r for r in rows if r.status == status]
        chosen.sort(key=key)
        return [
            _registration_dict(
                r,
                contacts=include_contacts,
                care_notes=include_care_notes,
                with_class=False,
                position=positions.get(r.id),
            )
            for r in chosen
        ]

    def by_name(enrollment):
        return (enrollment.child.first_name.lower(), enrollment.child.last_name.lower())

    S = Enrollment.Status
    return {
        **_class_dict(cls),
        "enrolled": dicts(S.ENROLLED, by_name),
        "offered": dicts(S.OFFERED, lambda e: (e.offer_expires_at is None, e.offer_expires_at)),
        "waitlisted": dicts(S.WAITLISTED, lambda e: positions.get(e.id, 0)),
        "requested": dicts(S.REQUESTED, lambda e: e.created_at),
        "cancellation_requests": sum(1 for r in rows if r.cancellation_requested),
    }


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
    age_min/age_max are a recommendation shown to parents, not a hard limit: a
    parent can register a child outside the range after confirming a warning.
    A capacity of 0 opens the class for the waiting list only: parents can
    register, but no place is given out until the capacity is raised.
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
    if capacity < 0:
        raise ValueError("capacity cannot be negative.")

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
    them back. Optional notes are shown against each cancelled date and are
    emailed to the enrolled families as the reason, in one message per child
    covering every date in the call. Dates already past are not announced, so
    `dates_announced` can be lower than the number cancelled. Lessons whose
    attendance has already been recorded cannot be cancelled. Use
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
    # Only the dates that were actually on are announced: re-cancelling a date
    # that is already off must not email anybody a second time.
    freshly_off = [s for s in sessions if not s.cancelled]
    with transaction.atomic():
        for session in sessions:
            session.cancelled = True
            if notes:
                session.notes = notes[:200]
            session.save(update_fields=["cancelled", "notes"])
        notice = notify_lessons_cancelled(cls, freshly_off)
    return {
        "cancelled": [s.date.isoformat() for s in sessions],
        "cancelled_total": cls.sessions.filter(cancelled=True).count(),
        "children_notified": notice.children,
        "dates_announced": notice.dates,
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
    list_registrations,
    get_class_register,
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
