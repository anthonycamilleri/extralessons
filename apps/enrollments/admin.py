from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.admin_permissions import SchoolAdminPermissionMixin
from apps.catalog.admin import ManagedByFilter, ScopedByClassMixin
from apps.catalog.models import ActivityClass

from . import services
from .models import Attendance, Enrollment
from .services import EnrollmentError


def guardian_contacts(child):
    """Guardians as 'Name <email> · phone' lines, for lists and CSV."""
    lines = []
    for guardian in child.guardians.all():
        line = str(guardian)
        if guardian.phone_e164:
            line += f" · {guardian.phone_e164}"
        lines.append(line)
    return lines


@admin.register(Enrollment)
class EnrollmentAdmin(SchoolAdminPermissionMixin, ScopedByClassMixin, admin.ModelAdmin):
    """Enrolments are read-only rows; every transition goes through the
    services, reached from the Requests page, the roster and the bulk actions."""

    class_lookup = "activity_class"
    school_admin_can = frozenset({"view", "change"})
    change_list_template = "admin/enrollments/enrollment/change_list.html"
    list_display = ["child", "activity_class", "status_badge", "created_at", "guardians"]
    list_filter = [
        ManagedByFilter.for_lookup("activity_class"),
        "status",
        "activity_class__term",
        "activity_class",
    ]
    list_select_related = ["child", "activity_class__term"]
    search_fields = [
        "child__first_name",
        "child__last_name",
        "activity_class__title",
        "child__guardians__email",
    ]
    readonly_fields = [f.name for f in Enrollment._meta.fields]
    actions = ["approve_requests", "reject_requests", "cancel_enrollments"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("child__guardians")

    def has_add_permission(self, request):
        # Enrollments are created through the registration flow, never by hand.
        return False

    def has_change_permission(self, request, obj=None):
        # No row is edited in a form; the verb stays so the desk buttons and
        # the bulk actions may run.
        return obj is None and super().has_change_permission(request, obj)

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="status status-{}">{}</span>',
            obj.status.lower(),
            obj.get_status_display(),
        )

    @admin.display(description="guardians")
    def guardians(self, obj):
        return format_html("<br>".join(["{}"] * len(lines)), *lines) if (
            lines := guardian_contacts(obj.child)
        ) else "—"

    # -- The desk: requests page and one-click transitions -------------------

    def get_urls(self):
        wrap = self.admin_site.admin_view
        desk = [
            path("requests/", wrap(self.requests_view), name="enrollments_enrollment_requests"),
            path(
                "<int:object_id>/approve/",
                wrap(self.approve_view),
                name="enrollments_enrollment_approve",
            ),
            path(
                "<int:object_id>/reject/",
                wrap(self.reject_view),
                name="enrollments_enrollment_reject",
            ),
            path(
                "<int:object_id>/offer/",
                wrap(self.offer_view),
                name="enrollments_enrollment_offer",
            ),
            path(
                "<int:object_id>/confirm-cancellation/",
                wrap(self.confirm_cancellation_view),
                name="enrollments_enrollment_confirm_cancellation",
            ),
            path(
                "<int:object_id>/keep-place/",
                wrap(self.keep_place_view),
                name="enrollments_enrollment_keep_place",
            ),
        ]
        # Before Django's own patterns: the trailing <path:object_id>/ would
        # otherwise swallow "requests/".
        return desk + super().get_urls()

    def requests_view(self, request):
        """Pending requests for this admin's classes, oldest first, with the
        class's availability and one-click approve/reject — and, below them,
        the families asking to cancel a confirmed place."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        user = request.user
        pending = Enrollment.objects.pending_for(user)
        cancellations = Enrollment.objects.cancellation_requests_for(user)

        # A super admin with classes of their own can narrow to just those.
        show_scope = user.is_super_admin and user.managed_classes.exists()
        scope = request.GET.get("scope") if show_scope else None
        if scope == "mine":
            pending = pending.filter(activity_class__administrators=user)
            cancellations = cancellations.filter(activity_class__administrators=user)
        elif show_scope:
            scope = "all"

        focus = None
        class_id = request.GET.get("class", "")
        if class_id.isdigit():
            focus = ActivityClass.objects.managed_by(user).filter(pk=class_id).first()
            if focus is not None:
                pending = pending.filter(activity_class=focus)
                cancellations = cancellations.filter(activity_class=focus)

        pending = list(
            pending.select_related("child", "activity_class__provider", "activity_class__term")
            .prefetch_related("child__guardians")
            .order_by("created_at")
        )
        # One COUNT query for every class on the page, not one per row.
        places_free = {
            cls.pk: cls.places_free
            for cls in ActivityClass.objects.filter(
                pk__in={e.activity_class_id for e in pending}
            ).with_counts()
        }
        for enrollment in pending:
            enrollment.places_free = places_free.get(enrollment.activity_class_id, 0)
        cancellations = list(
            cancellations.select_related(
                "child", "activity_class__provider", "activity_class__term", "cancel_requested_by"
            )
            .prefetch_related("child__guardians")
            .order_by("cancel_requested_at")
        )
        waiting = {
            cls.pk: cls.waitlist_count
            for cls in ActivityClass.objects.filter(
                pk__in={e.activity_class_id for e in cancellations}
            ).with_counts()
        }
        for enrollment in cancellations:
            enrollment.waitlist_count = waiting.get(enrollment.activity_class_id, 0)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": "Enrolment requests",
            "pending": pending,
            "cancellations": cancellations,
            "focus": focus,
            "scope": scope,
            "show_scope": show_scope,
            "is_super_admin": user.is_super_admin,
            "my_classes": user.managed_classes.filter(term__is_active=True).order_by("title"),
        }
        return TemplateResponse(request, "admin/enrollments/enrollment/requests.html", context)

    def _next_url(self, request):
        target = request.POST.get("next", "")
        if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return target
        return reverse("admin:enrollments_enrollment_requests")

    def _transition(self, request, object_id, transition, outcome):
        """Shared shape of approve/reject/offer: POST only, scoped lookup, one
        service call, a flash message, back to where the button was."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied
        enrollment = get_object_or_404(
            self.get_queryset(request).select_related("child", "activity_class"), pk=object_id
        )
        try:
            enrollment = transition(enrollment, request.user)
        except EnrollmentError as exc:
            messages.error(request, str(exc))
        else:
            level, text = outcome(enrollment)
            messages.add_message(request, level, text)
        return redirect(self._next_url(request))

    def approve_view(self, request, object_id):
        def outcome(enrollment):
            if enrollment.status == Enrollment.Status.ENROLLED:
                return messages.SUCCESS, (
                    f"{enrollment.child.full_name} enrolled in {enrollment.activity_class.title}."
                )
            return messages.WARNING, (
                f"{enrollment.activity_class.title} is full — "
                f"{enrollment.child.full_name} was added to the waiting list."
            )

        return self._transition(request, object_id, services.approve_request, outcome)

    def reject_view(self, request, object_id):
        def outcome(enrollment):
            return messages.INFO, (
                f"Request for {enrollment.child.full_name} rejected; the family has been notified."
            )

        return self._transition(request, object_id, services.reject_request, outcome)

    def offer_view(self, request, object_id):
        def outcome(enrollment):
            deadline = date_format(timezone.localtime(enrollment.offer_expires_at), "l j F, H:i")
            return messages.SUCCESS, (
                f"Seat offered to {enrollment.child.full_name}'s family — they have "
                f"until {deadline} to confirm."
            )

        return self._transition(request, object_id, services.offer_seat, outcome)

    def confirm_cancellation_view(self, request, object_id):
        def outcome(enrollment):
            return messages.SUCCESS, (
                f"{enrollment.child.full_name}'s place in {enrollment.activity_class.title} "
                "has been cancelled; the family has been told."
            )

        return self._transition(request, object_id, services.confirm_cancellation, outcome)

    def keep_place_view(self, request, object_id):
        def outcome(enrollment):
            return messages.INFO, (
                f"{enrollment.child.full_name} keeps their place in "
                f"{enrollment.activity_class.title}; the family has been told to expect "
                "a word from you."
            )

        return self._transition(request, object_id, services.decline_cancellation, outcome)

    # -- Bulk actions ---------------------------------------------------------

    @admin.action(description="Approve selected requests", permissions=["change"])
    def approve_requests(self, request, queryset):
        enrolled = waitlisted = skipped = 0
        for enrollment in queryset.filter(status=Enrollment.Status.REQUESTED):
            try:
                enrollment = services.approve_request(enrollment, request.user)
            except EnrollmentError:
                skipped += 1
                continue
            if enrollment.status == Enrollment.Status.ENROLLED:
                enrolled += 1
            else:
                waitlisted += 1
        parts = [f"{enrolled} enrolled"]
        if waitlisted:
            parts.append(f"{waitlisted} added to a waiting list (class full)")
        if skipped:
            parts.append(f"{skipped} skipped (class no longer open)")
        self.message_user(request, "Approved: " + ", ".join(parts) + ".")

    @admin.action(description="Reject selected requests", permissions=["change"])
    def reject_requests(self, request, queryset):
        rejected = 0
        for enrollment in queryset.filter(status=Enrollment.Status.REQUESTED):
            try:
                services.reject_request(enrollment, request.user)
            except EnrollmentError:
                continue
            rejected += 1
        self.message_user(
            request, f"Rejected {rejected} request(s); the families have been notified.",
            messages.WARNING,
        )

    @admin.action(description="Cancel selected enrollments (notifies families)", permissions=["change"])
    def cancel_enrollments(self, request, queryset):
        cancelled = 0
        for enrollment in queryset.filter(status__in=Enrollment.ACTIVE_STATUSES):
            try:
                services.cancel(enrollment, Enrollment.CancelReason.ADMIN, actor=request.user)
            except EnrollmentError:
                continue  # e.g. cancelled concurrently by the parent
            cancelled += 1
        self.message_user(
            request, f"Cancelled {cancelled} enrollment(s).", messages.WARNING
        )


@admin.register(Attendance)
class AttendanceAdmin(SchoolAdminPermissionMixin, ScopedByClassMixin, admin.ModelAdmin):
    class_lookup = "session__activity_class"
    school_admin_can = frozenset({"view"})
    list_display = ["session", "child", "present", "marked_by", "marked_at"]
    list_filter = ["present", "session__activity_class"]
