import csv
import datetime

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.accounts.admin_permissions import SchoolAdminPermissionMixin, is_school_admin
from apps.accounts.models import Child, User
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications import services as notification_services
from apps.notifications.forms import RichTextField
from apps.notifications.models import Broadcast

from .models import (
    ActivityClass,
    ClassSession,
    Holiday,
    Instructor,
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


def certificate_pill(instructor):
    """Where the certificate of police conduct stands, as an admin pill."""
    status = instructor.certificate_status
    if status == "checked":
        return format_html(
            '<span class="pill pill-ok">checked {}</span>',
            instructor.conduct_certificate_checked_at.strftime("%-d %b %Y"),
        )
    if status == "uploaded":
        return mark_safe('<span class="pill pill-warn">uploaded, to check</span>')
    return mark_safe('<span class="pill pill-bad">not uploaded</span>')


class InstructorInline(admin.TabularInline):
    """The provider's instructors at a glance; each links to its own page,
    where the certificate is."""

    model = Instructor
    extra = 0
    fields = ["user", "classes_taught", "certificate"]
    readonly_fields = ["classes_taught", "certificate"]
    autocomplete_fields = ["user"]
    show_change_link = True
    verbose_name_plural = "instructors (profiles, certificates and classes are on each instructor's page)"

    @admin.display(description="classes")
    def classes_taught(self, obj):
        if not obj.pk:
            return "—"
        return ", ".join(cls.title for cls in obj.classes.all()) or "—"

    @admin.display(description="police conduct certificate")
    def certificate(self, obj):
        return certificate_pill(obj) if obj.pk else "—"


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_email", "contact_phone", "member_count", "instructor_count"]
    search_fields = ["name"]
    filter_horizontal = ["members"]
    inlines = [InstructorInline]

    @admin.display(description="provider accounts")
    def member_count(self, obj):
        return obj.members.count()

    @admin.display(description="instructors")
    def instructor_count(self, obj):
        return obj.instructors.count()


class CertificateFilter(admin.SimpleListFilter):
    title = "police conduct certificate"
    parameter_name = "certificate"

    def lookups(self, request, model_admin):
        return [
            ("missing", "Not uploaded"),
            ("uploaded", "Uploaded, to check"),
            ("checked", "Checked"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "missing":
            return queryset.filter(conduct_certificate="")
        if self.value() == "uploaded":
            return queryset.exclude(conduct_certificate="").filter(
                conduct_certificate_checked_at__isnull=True
            )
        if self.value() == "checked":
            return queryset.exclude(conduct_certificate="").filter(
                conduct_certificate_checked_at__isnull=False
            )
        return queryset


class InstructorAdminForm(forms.ModelForm):
    """The classes live on ActivityClass.instructors; this form shows the
    relation from the instructor's side, limited to their provider's classes."""

    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Classes they teach",
    )

    class Meta:
        model = Instructor
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["classes"].queryset = self.instance.provider.classes.select_related(
                "term"
            ).order_by("-term__start_date", "title")
            if not self.is_bound:
                self.initial["classes"] = list(
                    self.instance.classes.values_list("pk", flat=True)
                )
        else:
            self.fields["classes"].help_text = "Save the instructor first, then pick their classes."

    def save(self, commit=True):
        instructor = super().save(commit=commit)
        if commit:
            instructor.classes.set(self.cleaned_data["classes"])
        return instructor


@admin.register(Instructor)
class InstructorAdmin(SchoolAdminPermissionMixin, admin.ModelAdmin):
    """The backend view of the people in front of the children.

    Regular admins see the instructors of their classes and can mark a
    certificate as checked, which is the office's one job here; the profile
    itself is the instructor's to write and the provider's to manage, so it
    is read-only for everyone but super admins. Read-only admins see every
    instructor and the certificate, and mark nothing.
    """

    school_admin_can = frozenset({"view"})
    list_display = [
        "display_name",
        "provider",
        "email",
        "classes_taught",
        "certificate",
        "profile_complete",
    ]
    list_filter = [CertificateFilter, "provider"]
    search_fields = ["user__first_name", "user__last_name", "user__email", "provider__name"]
    autocomplete_fields = ["user"]
    form = InstructorAdminForm
    actions = ["mark_certificate_checked"]
    readonly_fields = [
        "classes_taught",
        "certificate_link",
        "conduct_certificate_uploaded_at",
        "conduct_certificate_checked_at",
        "conduct_certificate_checked_by",
        "created_at",
    ]
    fieldsets = (
        (None, {"fields": ("provider", "user", "classes")}),
        ("Profile (shown to parents on the class page)", {"fields": ("bio", "photo")}),
        (
            "Certificate of police conduct",
            {
                "fields": (
                    "certificate_link",
                    "conduct_certificate",
                    "conduct_certificate_issued_on",
                    "conduct_certificate_uploaded_at",
                    "conduct_certificate_checked_at",
                    "conduct_certificate_checked_by",
                ),
                "description": "The document itself opens only for the office, the "
                "provider and the instructor. Parents are shown only that the school "
                "has checked it: use the “Mark certificate as checked” action on the "
                "list once you have looked at it. A new upload clears the check.",
            },
        ),
        ("Record", {"fields": ("created_at",)}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("user", "provider")
        if not request.user.sees_everything:
            qs = qs.filter(classes__in=ActivityClass.objects.managed_by(request.user)).distinct()
        return qs.prefetch_related("classes")

    def get_fieldsets(self, request, obj=None):
        # A regular admin gets the read-only page; the class picker is a form
        # field over a reverse relation, which the read-only renderer cannot
        # show, so they get the plain list of classes instead.
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets
        head, *rest = fieldsets
        fields = tuple("classes_taught" if f == "classes" else f for f in head[1]["fields"])
        return [(head[0], {**head[1], "fields": fields}), *rest]

    def save_related(self, request, form, formsets, change):
        # The ModelForm's own save_m2m knows nothing about the reverse
        # relation; the form's save(commit=True) path does, so call it.
        super().save_related(request, form, formsets, change)
        if "classes" in form.cleaned_data:
            form.instance.classes.set(form.cleaned_data["classes"])

    def save_model(self, request, obj, form, change):
        if "conduct_certificate" in form.changed_data and form.cleaned_data.get(
            "conduct_certificate"
        ):
            from django.utils import timezone

            obj.conduct_certificate_uploaded_at = timezone.now()
            obj.conduct_certificate_checked_at = None
            obj.conduct_certificate_checked_by = None
        super().save_model(request, obj, form, change)

    @admin.display(description="instructor", ordering="user__first_name")
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description="email", ordering="user__email")
    def email(self, obj):
        return obj.user.email

    @admin.display(description="classes")
    def classes_taught(self, obj):
        return ", ".join(cls.title for cls in obj.classes.all()) or "—"

    @admin.display(description="police conduct certificate")
    def certificate(self, obj):
        return certificate_pill(obj)

    @admin.display(description="profile", boolean=True)
    def profile_complete(self, obj):
        return bool(obj.bio)

    @admin.display(description="current file")
    def certificate_link(self, obj):
        if not obj.pk or not obj.has_certificate:
            return "No certificate uploaded yet."
        return format_html(
            '<a href="{}">Download the certificate</a>{}',
            reverse("provider_instructor_certificate", kwargs={"instructor_id": obj.pk}),
            f" · issued {obj.conduct_certificate_issued_on:%-d %b %Y}"
            if obj.conduct_certificate_issued_on
            else "",
        )

    def has_mark_certificate_permission(self, request):
        """The action's own verb: the acting admins, whose model permission
        here is view only, and superusers. Read-only admins look on."""
        return is_school_admin(request) or request.user.is_superuser

    @admin.action(
        description="Mark police conduct certificate as checked",
        permissions=["mark_certificate"],
    )
    def mark_certificate_checked(self, request, queryset):
        checked = sum(
            int(instructor.mark_certificate_checked(request.user)) for instructor in queryset
        )
        skipped = queryset.count() - checked
        message = f"{checked} certificate(s) marked as checked."
        if skipped:
            message += f" {skipped} instructor(s) skipped: nothing uploaded yet."
        self.message_user(request, message, messages.WARNING if skipped else messages.SUCCESS)


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

    @admin.action(description="Copy holidays into another school year…", permissions=["change"])
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
    """Show a regular admin only the rows of the classes they look after.

    `class_lookup` is the ORM path from the model to its ActivityClass ("" for
    ActivityClass itself). Superusers always see everything: they are the ones
    who hand classes out, and must be able to see a class to reassign it. So
    do read-only admins, whose whole purpose is the full picture.
    """

    class_lookup = ""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.sees_everything:
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


def may_send_announcements(request):
    """The rule the announcement composer itself applies (BroadcastAdmin).

    Asked here so the class list can offer the same job without the class
    admin growing its own idea of who may write to families.
    """
    return is_school_admin(request) or request.user.has_perm("notifications.add_broadcast")


class ClassAnnouncementForm(forms.Form):
    """Write to one class's families, addressed before it is opened.

    The main composer picks its classes from the active term; this one is
    already pointed at a single class, which is what makes it the way to
    write to a class that is cancelled or whose term is over — without those
    classes having to clutter the composer's picker.

    Each audience carries the number of families it would reach, counted for
    this class now. A cancelled class reads "Everyone with a live place — 0
    families", which is the whole reason the third audience exists.
    """

    audience = forms.ChoiceField(
        widget=forms.RadioSelect,
        initial=Broadcast.Audience.EVERYONE,
        label="Who gets it",
        help_text="Counted for this class as it stands now. Nobody is emailed "
        "twice: a parent with two children in the class gets one email.",
    )
    subject = forms.CharField(max_length=200)
    body_html = RichTextField()

    def __init__(self, activity_class, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def families(audience):
            reached = notification_services.broadcast_recipients([activity_class], audience)
            return notification_services.family_count_phrase(len(reached))

        self.fields["audience"].choices = [
            (value, f"{label} — {families(value)}")
            for value, label in Broadcast.Audience.choices
        ]


class RegisterChildForm(forms.Form):
    """The office adds a child to a class from its roster.

    The picker is the admin's own autocomplete, so the roster does not carry
    a list of every child in the school; it searches the children this
    administrator may see (the child list's scope: everyone for a super
    admin, the children of families already in their classes otherwise),
    and the same queryset validates the choice.
    """

    child = forms.ModelChoiceField(
        queryset=Child.objects.none(),
        label="Child",
        widget=AutocompleteSelect(
            Enrollment._meta.get_field("child"),
            admin.site,
            attrs={"data-placeholder": "Type the child's name…"},
        ),
    )

    def __init__(self, children, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = children


class ClassSessionInline(admin.TabularInline):
    model = ClassSession
    extra = 0
    fields = ["date", "cancelled", "holiday_override", "notes"]


class ActivityClassForm(forms.ModelForm):
    class Meta:
        model = ActivityClass
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "instructors" in self.fields and self.instance.pk:
            self.fields["instructors"].queryset = Instructor.objects.filter(
                provider=self.instance.provider_id
            ).select_related("user")

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("provider")
        strangers = [
            instructor
            for instructor in cleaned.get("instructors") or []
            if provider is not None and instructor.provider_id != provider.pk
        ]
        if strangers:
            names = ", ".join(instructor.display_name for instructor in strangers)
            self.add_error(
                "instructors",
                f"Only {provider.name}'s own instructors can teach this class ({names} "
                "belong to another provider).",
            )
        return cleaned

    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if self.instance.pk:
            # Counted directly rather than via places_free_now(), which floors
            # at zero and would hide seats offered beyond the old capacity.
            from apps.enrollments.services import _seats_taken

            seats_taken = _seats_taken(self.instance)
            if capacity < seats_taken:
                raise forms.ValidationError(
                    f"Capacity cannot go below the {seats_taken} seat(s) currently "
                    "held by enrolled children and outstanding offers. Cancel "
                    "enrolments first if the class must shrink."
                )
        return capacity


@admin.register(ActivityClass)
class ActivityClassAdmin(SchoolAdminPermissionMixin, ScopedByClassMixin, admin.ModelAdmin):
    """The class list doubles as the term's dashboard: every row carries the
    registration numbers, and links to its roster and its pending requests.

    Regular admins can look after their classes (edit, publish, cancel, take
    the roster) but not create, clone, archive or hand them out: that is
    setting up the programme, which stays with the super admins. Read-only
    admins get the whole dashboard and every roster, and none of the actions
    (each declares the verb it needs, which they never hold).
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
        "instructor_list",
        "administrator_list",
        "roster_link",
        "announce_link",
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
    filter_horizontal = ["administrators", "instructors"]
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
            .prefetch_related("administrators", "instructors__user")
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            for name in self.SUPERUSER_ACTIONS:
                actions.pop(name, None)
        return actions

    def get_list_display(self, request):
        # The Announce link opens the composer, which a read-only admin may
        # not use: no point in a column of links to a 403.
        columns = super().get_list_display(request)
        if request.user.is_read_only_admin:
            columns = [column for column in columns if column != "announce_link"]
        return columns

    def get_readonly_fields(self, request, obj=None):
        # Lifecycle changes must go through the actions (publish, cancel,
        # archive) so enrollments and notifications stay consistent — editing
        # the status directly would bypass cancel_class's bulk-cancel+notify.
        # Handing a class to someone is a super admin's call.
        readonly = ["status"] if obj else []
        if not request.user.is_superuser:
            readonly.append("administrators")
        return readonly

    def save_formset(self, request, form, formset, change):
        """Email the families about dates that this save turned off.

        The whole formset is one announcement: ticking three dates sends each
        family one email listing all three, with the note as the reason. The
        before/after comparison is done against the database rather than the
        forms, so a date arriving already cancelled — a new row, an import —
        counts the same as one ticked here, and a date that was already off
        stays quiet.
        """
        from apps.enrollments.services import notify_lessons_cancelled

        if formset.model is not ClassSession:
            super().save_formset(request, form, formset, change)
            return
        cancelled = ClassSession.objects.filter(
            activity_class=form.instance, cancelled=True
        )
        before = set(cancelled.values_list("pk", flat=True))
        super().save_formset(request, form, formset, change)
        notice = notify_lessons_cancelled(
            form.instance, list(cancelled.exclude(pk__in=before))
        )
        if notice.children:
            self.message_user(
                request,
                f"Emailed the families of {notice.children} child(ren) about "
                f"{notice.dates} cancelled date(s).",
            )

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

    @admin.display(description="instructors")
    def instructor_list(self, obj):
        return ", ".join(i.display_name for i in obj.instructors.all()) or "—"

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

    @admin.display(description="")
    def announce_link(self, obj):
        return format_html(
            '<a href="{}">Announce</a>',
            reverse("admin:catalog_activityclass_announce", kwargs={"object_id": obj.pk}),
        )

    # -- Roster ---------------------------------------------------------------

    def get_urls(self):
        extra = [
            path(
                "<int:object_id>/roster/",
                self.admin_site.admin_view(self.roster_view),
                name="catalog_activityclass_roster",
            ),
            path(
                "<int:object_id>/announce/",
                self.admin_site.admin_view(self.announce_view),
                name="catalog_activityclass_announce",
            ),
            path(
                "participants-by-day/",
                self.admin_site.admin_view(self.participants_by_day_view),
                name="catalog_activityclass_participants_by_day",
            ),
            path(
                "<int:object_id>/register/",
                self.admin_site.admin_view(self.register_view),
                name="catalog_activityclass_register",
            ),
        ]
        return extra + super().get_urls()

    def _children(self, request):
        """The children this administrator may register: the child list's
        own scope, asked of the ChildAdmin so it is defined once."""
        return self.admin_site.get_model_admin(Child).get_queryset(request)

    def roster_view(self, request, object_id):
        """Everyone in one class, by state, with the actions that move them —
        for whoever may move them; a read-only admin gets the lists alone."""
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
        from apps.enrollments.admin import flag_own_children

        flag_own_children(request.user, enrolled, offered, waitlisted, pending)

        if request.GET.get("format") == "csv":
            return self._roster_csv(cls, enrolled, offered, waitlisted, pending)

        # The buttons add, move and remove children: the same verb the
        # transitions check. A read-only admin gets the lists alone.
        can_act = self.has_change_permission(request, cls)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": f"{cls.title} · roster",
            "cls": cls,
            "can_act": can_act,
            "can_register": can_act
            and cls.status == ActivityClass.Status.PUBLISHED
            and cls.term.is_active,
            "register_form": RegisterChildForm(self._children(request)) if can_act else None,
            "enrolled": enrolled,
            "offered": offered,
            "waitlisted": waitlisted,
            "pending": pending,
            "seats_free": cls.places_free,
            "seats_over": max(0, cls.enrolled_count - cls.capacity),
            "instructors": list(cls.instructors.select_related("user")),
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

    def participants_by_day_view(self, request):
        """Every enrolled child of every class this term that is not
        cancelled, as a workbook with one sheet per weekday: the list the
        office prints for the gate or hands to the after-school staff."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        from openpyxl import Workbook
        from openpyxl.styles import Font

        classes = (
            self.get_queryset(request)
            .filter(term__is_active=True)
            .exclude(status=ActivityClass.Status.CANCELLED)
        )
        enrollments = (
            Enrollment.objects.filter(
                activity_class__in=classes, status=Enrollment.Status.ENROLLED
            )
            .select_related("child", "activity_class__provider")
            .order_by(
                "activity_class__weekday",
                "activity_class__start_time",
                "activity_class__title",
                "child__last_name",
                "child__first_name",
            )
        )
        days = dict(ActivityClass.WEEKDAYS)
        by_day = {}
        for enrollment in enrollments:
            by_day.setdefault(enrollment.activity_class.weekday, []).append(enrollment)

        workbook = Workbook()
        workbook.remove(workbook.active)
        headers = ["Student", "School class", "Class", "Time", "Location", "Provider"]
        for weekday in sorted(by_day) or [None]:
            sheet = workbook.create_sheet(days[weekday] if weekday is not None else "No participants")
            sheet.append(headers)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            sheet.freeze_panes = "A2"
            for enrollment in by_day.get(weekday, []):
                cls = enrollment.activity_class
                sheet.append(
                    [
                        enrollment.child.full_name,
                        enrollment.child.school_class,
                        cls.title,
                        f"{cls.start_time:%H:%M}–{cls.end_time:%H:%M}",
                        cls.location,
                        cls.provider.name,
                    ]
                )
            for column, width in zip("ABCDEF", [28, 12, 32, 13, 20, 24]):
                sheet.column_dimensions[column].width = width
            sheet.auto_filter.ref = sheet.dimensions

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="participants-by-day.xlsx"'
        workbook.save(response)
        return response

    def register_view(self, request, object_id):
        """The office adds a child to this class from the roster: enrolled
        straight away, no request to approve, the family told. POST only,
        back to the roster with the outcome as a message."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        cls = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, cls):
            raise PermissionDenied
        back = reverse("admin:catalog_activityclass_roster", kwargs={"object_id": cls.pk})
        form = RegisterChildForm(self._children(request), request.POST)
        if not form.is_valid():
            messages.error(request, "Pick a child from the list first.")
            return redirect(back)
        child = form.cleaned_data["child"]
        try:
            enrollment = enrollment_services.admin_register(child, cls, request.user)
        except EnrollmentError as exc:
            messages.error(request, str(exc))
            return redirect(back)
        from apps.enrollments.admin import placement_notes

        messages.success(
            request, f"{child.full_name} enrolled in {cls.title}; the family has been told."
        )
        for note in placement_notes(enrollment):
            messages.warning(request, note)
        return redirect(back)

    # -- Announcement to one class -------------------------------------------

    def announce_view(self, request, object_id):
        """Write to the families of one class, and nobody else.

        The composer under Notifications is the one for a message that spans
        classes, and its picker stays what it is: the active term. This page
        is the other half — the class is the address, so a cancelled class, or
        one whose term is over, can still be written to without being listed
        there.
        """
        cls = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not (self.has_change_permission(request, cls) and may_send_announcements(request)):
            raise PermissionDenied
        form = ClassAnnouncementForm(cls, request.POST or None)
        if request.method == "POST" and form.is_valid():
            _, count = notification_services.create_broadcast(
                sender=request.user,
                scope=Broadcast.Scope.SELECTED_CLASSES,
                subject=form.cleaned_data["subject"],
                body_html=form.cleaned_data["body_html"],
                classes=[cls],
                audience=form.cleaned_data["audience"],
            )
            # An audience can legitimately match nobody — a class with an
            # empty waiting list, or one whose places were never taken up.
            # Say so rather than reporting a send that reached no one.
            if count:
                self.message_user(
                    request,
                    f"Announcement queued for "
                    f"{notification_services.family_count_phrase(count)} of {cls.title}.",
                )
            else:
                self.message_user(
                    request,
                    "Nobody matched that audience, so the announcement was not sent "
                    "to anyone.",
                    messages.WARNING,
                )
            return redirect(reverse("admin:catalog_activityclass_changelist"))
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": f"{cls.title} · announcement",
            "cls": cls,
            "form": form,
        }
        return TemplateResponse(
            request, "admin/catalog/activityclass/announce.html", context
        )

    @admin.action(description="Assign administrators…", permissions=["change"])
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

    @admin.action(description="Publish and generate sessions", permissions=["change"])
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

    @admin.action(description="Regenerate sessions (skips school holidays)", permissions=["change"])
    def regenerate_sessions(self, request, queryset):
        """Re-run the calendar against the current schedule and holidays.

        The reconciliation is idempotent, so this is the safe button to press
        after moving a term, changing a weekday, or editing the holiday list.
        """
        plans = [generate_sessions(cls) for cls in queryset]
        self.message_user(
            request, f"Reconciled {len(plans)} class(es): {_totals(plans)}."
        )

    @admin.action(description="Clone into another term…", permissions=["change"])
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

    @admin.action(description="Cancel class (notifies all affected families)", permissions=["change"])
    def cancel_classes(self, request, queryset):
        from apps.enrollments.services import cancel_class

        for cls in queryset:
            cancel_class(cls)
        self.message_user(
            request,
            f"Cancelled {queryset.count()} class(es); affected families are being notified.",
            messages.WARNING,
        )

    @admin.action(description="Archive classes (only allowed with no active enrolments)", permissions=["change"])
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
                "enrolments — cancel the class (or its enrolments) first.",
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

    def save_model(self, request, obj, form, change):
        """The one-row screen announces a date the same way the inline does."""
        from apps.enrollments.services import notify_lessons_cancelled

        was_off = (
            ClassSession.objects.filter(pk=obj.pk)
            .values_list("cancelled", flat=True)
            .first()
            if change
            else False
        )
        super().save_model(request, obj, form, change)
        if obj.cancelled and not was_off:
            notice = notify_lessons_cancelled(obj.activity_class, [obj])
            if notice.children:
                self.message_user(
                    request,
                    f"Emailed the families of {notice.children} child(ren) about "
                    "this cancelled date.",
                )
