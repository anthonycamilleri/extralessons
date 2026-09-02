import datetime
from typing import NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Min, Q, Value
from django.db.models.functions import Greatest, Now
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone


class Provider(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="provider_orgs",
        help_text="Provider-role accounts that can manage this provider's classes.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SchoolYear(models.Model):
    """The school calendar a term hangs off: dates plus the holidays in them.

    Holidays are set once here, at system level, and every class in every term
    of the year inherits them — so nobody has to remember half-term when they
    publish a club.
    """

    name = models.CharField(max_length=100, unique=True, help_text="For example 2026/27.")
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gt=models.F("start_date")),
                name="school_year_dates_valid",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The school year must end after it starts."})


class Holiday(models.Model):
    """A closed period in a school year: half-term, Christmas, a public holiday.

    Inclusive of both end dates; a single day is start == end.
    """

    school_year = models.ForeignKey(
        SchoolYear, on_delete=models.CASCADE, related_name="holidays"
    )
    name = models.CharField(max_length=120, help_text="For example Christmas break.")
    start_date = models.DateField()
    end_date = models.DateField(
        help_text="Inclusive. For a single day, repeat the start date."
    )

    class Meta:
        verbose_name = "school holiday"
        verbose_name_plural = "school holidays"
        ordering = ["start_date", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="holiday_dates_valid",
            ),
            models.UniqueConstraint(
                fields=["school_year", "name", "start_date"], name="uniq_holiday_per_year"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.date_display})"

    @property
    def date_display(self):
        from django.utils.formats import date_format

        if self.start_date == self.end_date:
            return date_format(self.start_date, "j M Y")
        return (
            f"{date_format(self.start_date, 'j M')} – "
            f"{date_format(self.end_date, 'j M Y')}"
        )

    def covers(self, date):
        return self.start_date <= date <= self.end_date

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "A holiday cannot end before it starts."})
        year = self.school_year if self.school_year_id else None
        if year and self.start_date and self.end_date:
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError(
                    f"{year} runs {year.start_date} to {year.end_date}; this holiday "
                    "falls outside it."
                )


class Term(models.Model):
    school_year = models.ForeignKey(
        SchoolYear,
        on_delete=models.PROTECT,
        related_name="terms",
        null=True,
        blank=True,
        help_text="Terms in a school year inherit its holidays.",
    )
    name = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(
        default=False, help_text="Active terms are shown in the public catalogue."
    )

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gt=models.F("start_date")), name="term_dates_valid"
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The term must end after it starts."})
        year = self.school_year if self.school_year_id else None
        if year and self.start_date and self.end_date:
            if self.start_date < year.start_date or self.end_date > year.end_date:
                raise ValidationError(
                    f"{year} runs {year.start_date} to {year.end_date}; this term "
                    "falls outside it."
                )

    def holidays(self):
        """Holidays of this term's school year that overlap the term itself."""
        if not self.school_year_id:
            return Holiday.objects.none()
        return self.school_year.holidays.filter(
            start_date__lte=self.end_date, end_date__gte=self.start_date
        )


class ActivityClassQuerySet(models.QuerySet):
    def with_counts(self):
        from apps.enrollments.models import Enrollment

        # Same seat semantics as services._seats_taken: enrolled children plus
        # unexpired offers hold a seat; expired offers don't.
        return self.annotate(
            enrolled_count=Count(
                "enrollments",
                filter=Q(enrollments__status=Enrollment.Status.ENROLLED)
                | Q(
                    enrollments__status=Enrollment.Status.OFFERED,
                    enrollments__offer_expires_at__gte=Now(),
                ),
            ),
            waitlist_count=Count(
                "enrollments", filter=Q(enrollments__status=Enrollment.Status.WAITLISTED)
            ),
            requested_count=Count(
                "enrollments", filter=Q(enrollments__status=Enrollment.Status.REQUESTED)
            ),
        ).annotate(
            places_free=Greatest(F("capacity") - F("enrolled_count"), Value(0))
        )

    def published(self):
        return self.filter(status=ActivityClass.Status.PUBLISHED, term__is_active=True)

    def with_next_session(self):
        """Annotate the first and the next (from today) non-cancelled lesson date.

        Parents care about when the class actually meets next, not about the
        term's paperwork dates; `next_session_label` turns these two into the
        wording ("First class" before it starts, "Next class" afterwards).
        """
        today = timezone.localdate()
        live = Q(sessions__cancelled=False)
        return self.annotate(
            first_session_date=Min("sessions__date", filter=live),
            next_session_date=Min("sessions__date", filter=live & Q(sessions__date__gte=today)),
        )


class ActivityClass(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        CANCELLED = "CANCELLED", "Cancelled"
        ARCHIVED = "ARCHIVED", "Archived"

    WEEKDAYS = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="classes")
    term = models.ForeignKey(Term, on_delete=models.PROTECT, related_name="classes")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220)
    description = models.TextField(help_text="Shown on the public catalogue page.")
    extra_details = models.TextField(
        blank=True,
        help_text="Practical details shown on the class page: what to bring, "
        "meeting point, pickup arrangements...",
    )
    image = models.ImageField(
        upload_to="classes/",
        blank=True,
        help_text="Cover image shown in the catalogue.",
    )
    age_min = models.PositiveSmallIntegerField()
    age_max = models.PositiveSmallIntegerField()
    capacity = models.PositiveSmallIntegerField(default=15)
    weekday = models.SmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    location = models.CharField(max_length=200, blank=True)
    runs_during_holidays = models.BooleanField(
        default=False,
        help_text="School holidays are skipped when sessions are generated. "
        "Tick this for holiday camps and clubs that meet while school is out.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ActivityClassQuerySet.as_manager()

    class Meta:
        verbose_name = "class"
        verbose_name_plural = "classes"
        ordering = ["term", "weekday", "start_time", "title"]
        constraints = [
            models.UniqueConstraint(fields=["term", "slug"], name="uniq_slug_per_term"),
            models.CheckConstraint(condition=Q(age_max__gte=models.F("age_min")), name="age_range_valid"),
            models.CheckConstraint(condition=Q(capacity__gte=1), name="capacity_positive"),
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")), name="class_times_valid"
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.term})"

    def save(self, *args, **kwargs):
        # Optimize freshly uploaded images (an unsaved FieldFile is not
        # committed yet); already-stored files are left untouched. If the
        # caller passed update_fields, make sure the rewritten image is
        # included so the optimized file isn't silently dropped.
        if self.image and not self.image._committed:
            from .images import optimize_image

            self.image = optimize_image(self.image)
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"image"}

        # Detect capacity raises at the model layer so every edit path
        # (admin, shell, future views) alerts admins about offerable seats.
        old_capacity = None
        if self.pk and (
            kwargs.get("update_fields") is None or "capacity" in kwargs["update_fields"]
        ):
            old_capacity = (
                ActivityClass.objects.filter(pk=self.pk)
                .values_list("capacity", flat=True)
                .first()
            )
        super().save(*args, **kwargs)
        if old_capacity is not None and self.capacity > old_capacity:
            from apps.enrollments.services import capacity_increased

            capacity_increased(self)

    def get_absolute_url(self):
        return reverse("class_detail", kwargs={"term_id": self.term_id, "slug": self.slug})

    @property
    def schedule_display(self):
        return (
            f"{self.get_weekday_display()}s "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        )

    @property
    def next_session_label(self):
        """("First class" | "Next class", date) for the catalogue, or None.

        Needs the `with_next_session()` annotations; falls back to querying
        for a single instance so a template can never crash on a plain object.
        """
        if not hasattr(self, "next_session_date"):
            today = timezone.localdate()
            live = self.sessions.filter(cancelled=False)
            self.first_session_date = live.aggregate(d=Min("date"))["d"]
            self.next_session_date = live.filter(date__gte=today).aggregate(d=Min("date"))["d"]
        if self.next_session_date is None:
            return None
        if self.next_session_date == self.first_session_date:
            return ("First class", self.next_session_date)
        return ("Next class", self.next_session_date)

    def skipped_holidays(self):
        """School holidays that interrupt this class (empty if it runs through)."""
        if self.runs_during_holidays:
            return Holiday.objects.none()
        return self.term.holidays()

    def places_free_now(self):
        """Free seats for a single instance (querysets: use with_counts())."""
        from apps.enrollments.services import _seats_taken

        return max(0, self.capacity - _seats_taken(self))


class ClassSession(models.Model):
    activity_class = models.ForeignKey(
        ActivityClass, on_delete=models.CASCADE, related_name="sessions"
    )
    date = models.DateField()
    cancelled = models.BooleanField(default=False)
    notes = models.CharField(max_length=200, blank=True)
    holiday_override = models.BooleanField(
        default=False,
        verbose_name="runs despite holiday",
        help_text="Keep this session even though it falls in a school holiday. "
        "Set it on the one-off dates a class does meet over the break.",
    )

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["activity_class", "date"], name="uniq_session_date"),
        ]

    def __str__(self):
        return f"{self.activity_class.title} — {self.date}"


class SessionPlan(NamedTuple):
    """What a reconciliation pass did, for admin feedback."""

    created: int
    removed: int
    skipped: int

    @property
    def summary(self):
        parts = [f"{self.created} session(s) created"]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{self.skipped} date(s) skipped for school holidays")
        return ", ".join(parts)


def holiday_periods(activity_class):
    """The (start, end) ranges this class must skip, in date order.

    Empty when the term has no school year, or the class is flagged as running
    through the holidays — a holiday camp is exactly the case the default is
    wrong for.
    """
    if activity_class.runs_during_holidays:
        return []
    return list(activity_class.term.holidays().values_list("start_date", "end_date"))


def _in_holiday(date, periods):
    return any(start <= date <= end for start, end in periods)


def generate_sessions(activity_class):
    """Reconcile ClassSession rows with the class schedule and the school calendar.

    Idempotent: existing matching rows are kept, missing ones are created.
    Dates inside a school holiday of the term's school year are skipped, unless
    the class runs through holidays or the individual session is flagged as an
    override.

    Future sessions that no longer belong — wrong weekday after a schedule
    change, or newly covered by a holiday — are removed, unless attendance was
    already taken for them (those are kept as history and left for the admin to
    judge).
    """
    from django.utils import timezone

    term = activity_class.term
    periods = holiday_periods(activity_class)
    overridden = set(
        activity_class.sessions.filter(holiday_override=True).values_list("date", flat=True)
    )

    current = term.start_date
    # advance to the first occurrence of the class weekday
    offset = (activity_class.weekday - current.weekday()) % 7
    current += datetime.timedelta(days=offset)
    created = skipped = 0
    while current <= term.end_date:
        if _in_holiday(current, periods) and current not in overridden:
            skipped += 1
        else:
            _, was_created = ClassSession.objects.get_or_create(
                activity_class=activity_class, date=current
            )
            created += int(was_created)
        current += datetime.timedelta(days=7)

    today = timezone.localdate()
    removed = 0
    for session in activity_class.sessions.filter(date__gte=today):
        stale_weekday = session.date.weekday() != activity_class.weekday
        in_holiday = not session.holiday_override and _in_holiday(session.date, periods)
        if (stale_weekday or in_holiday) and not session.attendance.exists():
            session.delete()
            removed += 1
    return SessionPlan(created=created, removed=removed, skipped=skipped)


def apply_holidays(school_year):
    """Re-reconcile every class in a school year after its holidays changed.

    Only classes that already have sessions are touched: generating sessions is
    still the publish step's job, and a draft class should not acquire a
    calendar as a side effect of someone editing the holiday list.
    """
    classes = (
        ActivityClass.objects.filter(term__school_year=school_year, sessions__isnull=False)
        .select_related("term", "term__school_year")
        .distinct()
    )
    return [generate_sessions(cls) for cls in classes]


@receiver([post_save, post_delete], sender=Holiday)
def _reconcile_sessions_after_holiday_change(sender, instance, **kwargs):
    """Adding, moving or removing a holiday takes the sessions with it.

    A signal rather than a save() override because the admin deletes selected
    holidays with a bulk queryset delete, which never calls the model's own
    delete(). The school year is re-fetched so a cascade from deleting the
    year itself is a no-op rather than an error.
    """
    year = SchoolYear.objects.filter(pk=instance.school_year_id).first()
    if year is not None:
        apply_holidays(year)
