import csv
import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from apps.accounts.admin_permissions import SchoolAdminPermissionMixin
from apps.accounts.models import User

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


class ScopedByClassMixin:
    """Show a non-superuser admin only the rows of the classes they look after.

    `class_lookup` is the ORM path from the model to its ActivityClass ("" for
    ActivityClass itself). Superusers always see everything: they are the ones
    who hand classes out, and must be able to see a class to reassign it.
    """

    class_lookup = ""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        scope = ActivityClass.objects.managed_by(request.user)
        lookup = f"{self.class_lookup}__in" if self.class_lookup else "pk__in"
        return qs.filter(**{lookup: scope})


class ManagedByFilter(admin.SimpleListFilter):
    """Changelist filter "looked after by: me / nobody yet".

    The Django-native form of the old Only mine / All switch; `for_lookup`
    builds the variant for models that reach the class through a relation.
    """

    title = "looked after by"
    parameter_name = "who"
    lookup_prefix = ""

    @classmethod
    def for_lookup(cls, prefix):
        return type(f"ManagedByFilter_{prefix}", (cls,), {"lookup_prefix": prefix})

    def lookups(self, request, model_admin):
        return [("mine", "Me"), ("unassigned", "Nobody yet")]

    def queryset(self, request, queryset):
        field = f"{self.lookup_prefix}__administrators" if self.lookup_prefix else "administrators"
        if self.value() == "mine":
            return queryset.filter(**{field: request.user})
        if self.value() == "unassigned":
            return queryset.filter(**{f"{field}__isnull": True})
        return queryset


class AssignAdministratorsForm(forms.Form):
    administrators = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Administrators",
    )
    replace = forms.BooleanField(
        required=False,
        label="Replace the current administrators",
        help_text="Unticked, the people chosen above are added to whoever already "
        "looks after each class.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["administrators"].queryset = User.objects.active_admins().order_by(
            "first_name", "last_name", "email"
        )


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
class ActivityClassAdmin(SchoolAdminPermissionMixin, ScopedByClassMixin, admin.ModelAdmin):
    """The class list doubles as the term's dashboard: every row carries the
    registration numbers, and links to its roster and its pending requests.

    Regular admins can look after their classes (edit, publish, cancel, take
    the roster) but not create, clone, archive or hand them out: that is
    setting up the programme, which stays with the super admins.
    """

    school_admin_can = frozenset({"view", "change"})
    change_form_template = "admin/catalog/activityclass/change_form.html"
    list_display = [
        "title",
        "term",
        "schedule_display",
        "registrations",
        "confirmed",
        "available",
        "waiting",
        "pending",
        "status",
        "administrator_list",
        "roster_link",
    ]
    list_filter = [
        ManagedByFilter,
        # "active term" first: the desk links land here with ?term__is_active__exact=1
        "term__is_active",
        "term",
        "status",
        "provider",
        "administrators",
        "runs_during_holidays",
    ]
    search_fields = ["title", "provider__name", "administrators__email"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["administrators"]
    inlines = [ClassSessionInline]
    form = ActivityClassForm
    actions = [
        "publish_classes",
        "regenerate_sessions",
        "assign_administrators",
        "clone_into_term",
        "cancel_classes",
        "archive_classes",
    ]
    SUPERUSER_ACTIONS = {"assign_administrators", "clone_into_term", "archive_classes"}

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .with_counts()
            .select_related("term", "provider")
            .prefetch_related("administrators")
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            for name in self.SUPERUSER_ACTIONS:
                actions.pop(name, None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        # Lifecycle changes must go through the actions (publish, cancel,
        # archive) so enrollments and notifications stay consistent — editing
        # the status directly would bypass cancel_class's bulk-cancel+notify.
        # Handing a class to someone is a super admin's call.
        readonly = ["status"] if obj else []
        if not request.user.is_superuser:
            readonly.append("administrators")
        return readonly

    # -- Dashboard columns (annotations from with_counts) ---------------------

    @admin.display(description="registrations", ordering="registrations_count")
    def registrations(self, obj):
        return obj.registrations_count

    @admin.display(description="confirmed", ordering="confirmed_count")
    def confirmed(self, obj):
        text = f"{obj.confirmed_count} / {obj.capacity}"
        if obj.offered_count:
            text += f" (+{obj.offered_count} offered)"
        return text

    @admin.display(description="available", ordering="places_available")
    def available(self, obj):
        css = "pill-ok" if obj.places_available > 0 else "pill-warn"
        return format_html('<span class="pill {}">{}</span>', css, obj.places_available)

    @admin.display(description="waiting", ordering="waitlist_count")
    def waiting(self, obj):
        return obj.waitlist_count

    @admin.display(description="pending", ordering="requested_count")
    def pending(self, obj):
        if not obj.requested_count:
            return "0"
        url = reverse("admin:enrollments_enrollment_requests")
        return format_html('<a href="{}?class={}"><b>{}</b></a>', url, obj.pk, obj.requested_count)

    @admin.display(description="administrators")
    def administrator_list(self, obj):
        names = [admin.get_full_name() or admin.email for admin in obj.administrators.all()]
        return ", ".join(names) or "—"

    @admin.display(description="")
    def roster_link(self, obj):
        return format_html(
            '<a href="{}">Roster</a>',
            reverse("admin:catalog_activityclass_roster", kwargs={"object_id": obj.pk}),
        )

    # -- Roster ---------------------------------------------------------------

    def get_urls(self):
        roster = [
            path(
                "<int:object_id>/roster/",
                self.admin_site.admin_view(self.roster_view),
                name="catalog_activityclass_roster",
            ),
        ]
        return roster + super().get_urls()

    def roster_view(self, request, object_id):
        """Everyone in one class, by state, with the actions that move them."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        cls = get_object_or_404(self.get_queryset(request), pk=object_id)

        def people(queryset):
            return list(queryset.select_related("child").prefetch_related("child__guardians"))

        from apps.enrollments.models import Enrollment

        S = Enrollment.Status
        enrolled = people(
            cls.enrollments.filter(status=S.ENROLLED).order_by(
                "child__first_name", "child__last_name"
            )
        )
        offered = people(cls.enrollments.filter(status=S.OFFERED).order_by("offer_expires_at"))
        waitlisted = people(cls.enrollments.waitlist_fifo())
        pending = people(cls.enrollments.filter(status=S.REQUESTED).order_by("created_at"))
        for enrollment in pending:
            enrollment.places_free = cls.places_free

        if request.GET.get("format") == "csv":
            return self._roster_csv(cls, enrolled, offered, waitlisted, pending)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": f"{cls.title} · roster",
            "cls": cls,
            "enrolled": enrolled,
            "offered": offered,
            "waitlisted": waitlisted,
            "pending": pending,
            "seats_free": cls.places_free,
        }
        return TemplateResponse(request, "admin/catalog/activityclass/roster.html", context)

    def _roster_csv(self, cls, enrolled, offered, waitlisted, pending):
        from apps.enrollments.admin import guardian_contacts

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="roster-{cls.term.name}-{cls.slug}.csv"'
        )
        response.write("\ufeff")  # BOM: Excel then reads the UTF-8 correctly
        writer = csv.writer(response)
        writer.writerow(
            [
                "Status",
                "Position",
                "Child",
                "Date of birth",
                "School class",
                "May leave alone",
                "Notes",
                "Guardians",
                "Since",
            ]
        )
        groups = [
            ("Enrolled", enrolled, lambda e: e.enrolled_at),
            ("Offered", offered, lambda e: e.offered_at),
            ("Waiting", waitlisted, lambda e: e.waitlisted_at),
            ("Requested", pending, lambda e: e.created_at),
        ]
        for label, rows, since in groups:
            for position, enrollment in enumerate(rows, start=1):
                child = enrollment.child
                stamp = since(enrollment)
                writer.writerow(
                    [
                        label,
                        position if label == "Waiting" else "",
                        child.full_name,
                        child.date_of_birth.isoformat(),
                        child.school_class,
                        "yes" if child.may_leave_alone else "no",
                        child.notes,
                        "; ".join(guardian_contacts(child)),
                        stamp.date().isoformat() if stamp else "",
                    ]
                )
        return response

    @admin.action(description="Assign administrators…")
    def assign_administrators(self, request, queryset):
        """Hand a batch of classes to one or more admins in one go.

        Assigning forty classes one edit form at a time is what would stop
        anyone from using the feature; this is the bulk path.
        """
        if "apply" in request.POST:
            form = AssignAdministratorsForm(request.POST)
            if form.is_valid():
                admins = form.cleaned_data["administrators"]
                for cls in queryset:
                    if form.cleaned_data["replace"]:
                        cls.administrators.set(admins)
                    else:
                        cls.administrators.add(*admins)
                names = ", ".join(a.get_full_name() or a.email for a in admins)
                self.message_user(
                    request,
                    f"{queryset.count()} class(es) now looked after by {names}.",
                )
                return redirect(reverse("admin:catalog_activityclass_changelist"))
        else:
            form = AssignAdministratorsForm()
        return render(
            request,
            "admin/catalog/assign_administrators.html",
            {"classes": queryset, "form": form, "title": "Assign administrators"},
        )

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
class ClassSessionAdmin(SchoolAdminPermissionMixin, ScopedByClassMixin, admin.ModelAdmin):
    class_lookup = "activity_class"
    school_admin_can = frozenset({"view", "change"})
    list_display = ["activity_class", "date", "cancelled", "holiday_override"]
    list_filter = ["activity_class__term", "cancelled", "holiday_override"]
    date_hierarchy = "date"
