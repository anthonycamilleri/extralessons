import datetime

from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.urls import reverse


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


class Term(models.Model):
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


class ActivityClassQuerySet(models.QuerySet):
    def with_counts(self):
        from apps.enrollments.models import Enrollment

        return self.annotate(
            enrolled_count=Count(
                "enrollments",
                filter=Q(enrollments__status__in=Enrollment.SEAT_HOLDING_STATUSES),
            ),
            waitlist_count=Count(
                "enrollments", filter=Q(enrollments__status=Enrollment.Status.WAITLISTED)
            ),
            requested_count=Count(
                "enrollments", filter=Q(enrollments__status=Enrollment.Status.REQUESTED)
            ),
        )

    def published(self):
        return self.filter(status=ActivityClass.Status.PUBLISHED, term__is_active=True)


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

    def get_absolute_url(self):
        return reverse("class_detail", kwargs={"term_id": self.term_id, "slug": self.slug})

    @property
    def schedule_display(self):
        return (
            f"{self.get_weekday_display()}s "
            f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        )

    def seat_holders(self):
        """Enrollments currently holding a seat (enrolled or offered)."""
        from apps.enrollments.models import Enrollment

        return self.enrollments.filter(status__in=Enrollment.SEAT_HOLDING_STATUSES)

    def places_remaining(self):
        return max(0, self.capacity - self.seat_holders().count())


class ClassSession(models.Model):
    activity_class = models.ForeignKey(
        ActivityClass, on_delete=models.CASCADE, related_name="sessions"
    )
    date = models.DateField()
    cancelled = models.BooleanField(default=False)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["activity_class", "date"], name="uniq_session_date"),
        ]

    def __str__(self):
        return f"{self.activity_class.title} — {self.date}"


def generate_sessions(activity_class):
    """Create ClassSession rows for every matching weekday within the term.

    Idempotent: existing rows are kept, missing ones are created.
    """
    term = activity_class.term
    current = term.start_date
    # advance to the first occurrence of the class weekday
    offset = (activity_class.weekday - current.weekday()) % 7
    current += datetime.timedelta(days=offset)
    created = 0
    while current <= term.end_date:
        _, was_created = ClassSession.objects.get_or_create(
            activity_class=activity_class, date=current
        )
        created += int(was_created)
        current += datetime.timedelta(days=7)
    return created
