from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Prefetch

from apps.catalog.admin import ScopedByClassMixin
from apps.catalog.models import ActivityClass
from apps.enrollments.models import Enrollment

from .admin_permissions import SchoolAdminPermissionMixin, is_school_admin
from .models import Child, Guardian, GuardianInvite, SiteConfig, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "role", "is_active"]
    list_filter = ["role", "is_active", "notify_email", "notify_whatsapp"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone_e164")}),
        ("Role & notifications", {"fields": ("role", "notify_email", "notify_whatsapp")}),
        (
            "Permissions",
            {
                "fields": ("is_active", "is_staff", "is_superuser", "groups"),
                "description": "Every account with the School admin role can use this admin "
                "for the classes assigned to them (set on each class, or with the class "
                "list's “Assign administrators” action): their requests, rosters, waiting "
                "lists, sessions, the children in them and announcements to their families. "
                "Superuser status makes them a <b>super admin</b>: every class, plus the "
                "programme itself (school years, terms, providers, new classes, accounts, "
                "settings). Alerts for a class with nobody assigned go to the super admins.",
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "role", "password1", "password2")}),
    )


class GuardianInline(admin.TabularInline):
    """Who may manage the child. Superusers link accounts here; other admins
    read the contact details (the account picker searches the user list,
    which they cannot see)."""

    model = Guardian
    extra = 0
    readonly_fields = ["guardian_name", "guardian_email", "guardian_phone"]

    def get_fields(self, request, obj=None):
        # The account link would 403 for a regular admin: show the name instead.
        who = "user" if request.user.is_superuser else "guardian_name"
        return [who, "is_primary", "guardian_email", "guardian_phone"]

    def get_autocomplete_fields(self, request):
        return ["user"] if request.user.is_superuser else []

    def has_view_permission(self, request, obj=None):
        return is_school_admin(request) or super().has_view_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="guardian")
    def guardian_name(self, obj):
        return (obj.user.get_full_name() or obj.user.email) if obj.pk else "—"

    @admin.display(description="email")
    def guardian_email(self, obj):
        return obj.user.email if obj.pk else "—"

    @admin.display(description="phone")
    def guardian_phone(self, obj):
        return (obj.user.phone_e164 or "—") if obj.pk else "—"


class EnrollmentInline(ScopedByClassMixin, admin.TabularInline):
    """Everything the child was ever registered for, cancelled included: the
    history is what answers "why was my child removed?"."""

    model = Enrollment
    class_lookup = "activity_class"
    fk_name = "child"
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["activity_class", "status", "created_at", "cancel_reason"]
    readonly_fields = fields
    ordering = ["-created_at"]
    verbose_name = "registration"
    verbose_name_plural = "registrations"

    def has_view_permission(self, request, obj=None):
        return is_school_admin(request) or super().has_view_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Child)
class ChildAdmin(SchoolAdminPermissionMixin, admin.ModelAdmin):
    """Regular admins see the children in their classes, read-only, with the
    guardians' contact details and every registration."""

    school_admin_can = frozenset({"view"})
    list_display = [
        "first_name",
        "last_name",
        "school_class",
        "date_of_birth",
        "may_leave_alone",
        "registered_for",
        "guardian_list",
    ]
    list_filter = ["school_class", "may_leave_alone"]
    search_fields = ["first_name", "last_name", "guardians__email", "guardians__phone_e164"]
    inlines = [GuardianInline, EnrollmentInline]

    def get_queryset(self, request):
        managed = ActivityClass.objects.managed_by(request.user)
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            qs = qs.filter(enrollments__activity_class__in=managed).distinct()
        return qs.prefetch_related(
            "guardians",
            Prefetch(
                "enrollments",
                queryset=Enrollment.objects.filter(
                    status__in=Enrollment.ACTIVE_STATUSES, activity_class__in=managed
                ).select_related("activity_class"),
                to_attr="active_enrollments",
            ),
        )

    @admin.display(description="registered for")
    def registered_for(self, obj):
        return (
            ", ".join(
                f"{e.activity_class.title} ({e.get_status_display()})"
                for e in obj.active_enrollments
            )
            or "—"
        )

    @admin.display(description="Guardians")
    def guardian_list(self, obj):
        lines = []
        for guardian in obj.guardians.all():
            line = str(guardian)
            if guardian.phone_e164:
                line += f" · {guardian.phone_e164}"
            lines.append(line)
        return ", ".join(lines)


@admin.register(GuardianInvite)
class GuardianInviteAdmin(admin.ModelAdmin):
    list_display = ["email", "child", "invited_by", "created_at", "accepted_at"]
    readonly_fields = ["token"]


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        ("School", {"fields": ("school_name", "sender_name", "contact_email", "catalogue_intro")}),
        ("Registrations", {"fields": ("signup_open", "offer_ttl_hours")}),
        ("Admin alerts", {"fields": ("notify_admins_new_request", "notify_admins_seat_freed")}),
        (
            "Terms and conditions",
            {
                "fields": ("terms_markdown",),
                "description": "Markdown: use # for the title, ## for section headings, "
                "blank lines between paragraphs.",
            },
        ),
    )

    def has_add_permission(self, request):
        # Singleton: only editable, never added (the row is auto-created).
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
