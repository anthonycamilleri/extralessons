import datetime

from django.conf import settings
from django.db import models
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Greatest, Now
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

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["activity_class", "date"], name="uniq_session_date"),
        ]

    def __str__(self):
        return f"{self.activity_class.title} — {self.date}"


def generate_sessions(activity_class):
    """Reconcile ClassSession rows with the class schedule.

    Idempotent: existing matching rows are kept, missing ones are created.
    If the weekday changed since sessions were generated, future sessions on
    the wrong weekday are removed — unless attendance was already taken for
    them (those are kept as history and left for the admin to judge).
    """
    from django.utils import timezone

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

    today = timezone.localdate()
    for session in activity_class.sessions.filter(date__gte=today):
        if session.date.weekday() != activity_class.weekday and not session.attendance.exists():
            session.delete()
    return created
