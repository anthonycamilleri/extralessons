import datetime

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import (
    ActivityClass,
    ClassSession,
    Holiday,
    Provider,
    SchoolYear,
    SessionPlan,
    Term,
    generate_sessions,
)


def _totals(plans):
    """One sentence for a batch of reconciliation passes."""
    return SessionPlan(
        created=sum(plan.created for plan in plans),
        removed=sum(plan.removed for plan in plans),
        skipped=sum(plan.skipped for plan in plans),
    ).summary


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_email", "contact_phone"]
    search_fields = ["name"]
    filter_horizontal = ["members"]


class HolidayInline(admin.TabularInline):
    model = Holiday
    extra = 3
    fields = ["name", "start_date", "end_date"]


class CopyHolidaysForm(forms.Form):
    target_year = forms.ModelChoiceField(
        queryset=SchoolYear.objects.all(),
        label="Copy the holidays of the selected year into",
        help_text="Dates are shifted by whole weeks so they land on the same "
        "weekdays; check them against the published calendar afterwards.",
    )


@admin.register(SchoolYear)
class SchoolYearAdmin(admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date", "holiday_count", "term_count"]
    inlines = [HolidayInline]
    actions = ["copy_holidays"]

    @admin.display(description="holidays")
    def holiday_count(self, obj):
        return obj.holidays.count()

    @admin.display(description="terms")
    def term_count(self, obj):
        return obj.terms.count()

    @admin.action(description="Copy holidays into another school year…")
    def copy_holidays(self, request, queryset):
        """Set up next year's calendar from this year's in one step.

        Shifting by whole weeks keeps half-term on a Monday-to-Friday; the
        office still has to check the result, which is why the form says so.
        """
        if queryset.count() != 1:
            self.message_user(
                request, "Pick exactly one school year to copy from.", messages.ERROR
            )
            return None
        source = queryset.get()
        if "apply" in request.POST:
            form = CopyHolidaysForm(request.POST)
            if form.is_valid():
                target = form.cleaned_data["target_year"]
                if target == source:
                    self.message_user(
                        request, "Source and target are the same year.", messages.ERROR
                    )
                    return redirect(reverse("admin:catalog_schoolyear_changelist"))
                shift = datetime.timedelta(
                    days=round((target.start_date - source.start_date).days / 7) * 7
                )
                copied = 0
                for holiday in source.holidays.all():
                    start, end = holiday.start_date + shift, holiday.end_date + shift
                    if not (target.start_date <= start and end <= target.end_date):
                        continue
                    _, created = Holiday.objects.get_or_create(
                        school_year=target,
                        name=holiday.name,
                        start_date=start,
                        defaults={"end_date": end},
                    )
                    copied += int(created)
                self.message_user(
                    request,
                    f"Copied {copied} holiday period(s) into {target}. Check the "
                    "dates against the published school calendar.",
                )
                return redirect(reverse("admin:catalog_schoolyear_changelist"))
        else:
            form = CopyHolidaysForm()
        return render(
            request,
            "admin/catalog/copy_holidays.html",
            {"source": source, "form": form, "title": "Copy school holidays"},
        )


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ["name", "school_year", "start_date", "end_date"]
    list_filter = ["school_year"]
    search_fields = ["name"]
    date_hierarchy = "start_date"


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ["name", "school_year", "start_date", "end_date", "is_active"]
    list_filter = ["is_active", "school_year"]


class CloneIntoTermForm(forms.Form):
    target_term = forms.ModelChoiceField(
        queryset=Term.objects.all(), label="Copy the selected classes into term"
    )


class ClassSessionInline(admin.TabularInline):
    model = ClassSession
    extra = 0
    fields = ["date", "cancelled", "holiday_override", "notes"]


class ActivityClassForm(forms.ModelForm):
    class Meta:
        model = ActivityClass
        fields = "__all__"

    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if self.instance.pk:
            seats_taken = self.instance.capacity - self.instance.places_free_now()
            if capacity < seats_taken:
                raise forms.ValidationError(
                    f"Capacity cannot go below the {seats_taken} seat(s) currently "
                    "held by enrolled children and outstanding offers. Cancel "
                    "enrollments first if the class must shrink."
                )
        return capacity


@admin.register(ActivityClass)
class ActivityClassAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "term",
        "provider",
        "schedule_display",
        "capacity",
        "status",
    ]
    list_filter = ["term", "status", "provider", "runs_during_holidays"]
    search_fields = ["title", "provider__name"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [ClassSessionInline]
    form = ActivityClassForm
    actions = [
        "publish_classes",
        "regenerate_sessions",
        "clone_into_term",
        "cancel_classes",
        "archive_classes",
    ]

    def get_readonly_fields(self, request, obj=None):
        # Lifecycle changes must go through the actions (publish, cancel,
        # archive) so enrollments and notifications stay consistent — editing
        # the status directly would bypass cancel_class's bulk-cancel+notify.
        return ["status"] if obj else []

    @admin.action(description="Publish and generate sessions")
    def publish_classes(self, request, queryset):
        published = 0
        plans = []
        for cls in queryset.exclude(status=ActivityClass.Status.CANCELLED):
            cls.status = ActivityClass.Status.PUBLISHED
            cls.save(update_fields=["status"])
            plans.append(generate_sessions(cls))
            published += 1
        self.message_user(
            request, f"Published {published} class(es): {_totals(plans)}."
        )

    @admin.action(description="Regenerate sessions (skips school holidays)")
    def regenerate_sessions(self, request, queryset):
        """Re-run the calendar against the current schedule and holidays.

        The reconciliation is idempotent, so this is the safe button to press
        after moving a term, changing a weekday, or editing the holiday list.
        """
        plans = [generate_sessions(cls) for cls in queryset]
        self.message_user(
            request, f"Reconciled {len(plans)} class(es): {_totals(plans)}."
        )

    @admin.action(description="Clone into another term…")
    def clone_into_term(self, request, queryset):
        if "apply" in request.POST:
            form = CloneIntoTermForm(request.POST)
            if form.is_valid():
                target = form.cleaned_data["target_term"]
                cloned = 0
                for cls in queryset:
                    if ActivityClass.objects.filter(term=target, slug=cls.slug).exists():
                        continue
                    cls.pk = None
                    cls._state.adding = True
                    cls.term = target
                    cls.status = ActivityClass.Status.DRAFT
                    cls.save()
                    cloned += 1
                self.message_user(
                    request,
                    f"Cloned {cloned} class(es) into {target} as drafts "
                    "(already-existing slugs were skipped).",
                )
                return redirect(reverse("admin:catalog_activityclass_changelist"))
        else:
            form = CloneIntoTermForm()
        return render(
            request,
            "admin/catalog/clone_into_term.html",
            {"classes": queryset, "form": form, "title": "Clone classes into term"},
        )

    @admin.action(description="Cancel class (notifies all affected families)")
    def cancel_classes(self, request, queryset):
        from apps.enrollments.services import cancel_class

        for cls in queryset:
            cancel_class(cls)
        self.message_user(
            request,
            f"Cancelled {queryset.count()} class(es); affected families are being notified.",
            messages.WARNING,
        )

    @admin.action(description="Archive classes (only allowed with no active enrollments)")
    def archive_classes(self, request, queryset):
        from apps.enrollments.models import Enrollment

        blocked = queryset.filter(
            enrollments__status__in=Enrollment.ACTIVE_STATUSES
        ).distinct()
        archivable = queryset.exclude(pk__in=blocked)
        archived = archivable.update(status=ActivityClass.Status.ARCHIVED)
        if blocked:
            self.message_user(
                request,
                f"Skipped {blocked.count()} class(es) that still have active "
                "enrollments — cancel the class (or its enrollments) first.",
                messages.WARNING,
            )
        if archived:
            self.message_user(request, f"Archived {archived} class(es).")


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = ["activity_class", "date", "cancelled", "holiday_override"]
    list_filter = ["activity_class__term", "cancelled", "holiday_override"]
    date_hierarchy = "date"
