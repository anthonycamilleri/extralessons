from django.contrib import admin, messages

from .models import Attendance, Enrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["child", "activity_class", "status", "created_at", "cancel_reason"]
    list_filter = ["status", "activity_class__term", "activity_class"]
    search_fields = ["child__first_name", "child__last_name", "activity_class__title"]
    readonly_fields = [
        "status",
        "created_at",
        "approved_at",
        "waitlisted_at",
        "offered_at",
        "offer_expires_at",
        "enrolled_at",
        "cancelled_at",
        "cancel_reason",
        "decided_by",
        "promoted_from_waitlist",
    ]
    actions = ["cancel_enrollments"]

    def has_add_permission(self, request):
        # Enrollments are created through the registration flow, never by hand.
        return False

    @admin.action(description="Cancel selected enrollments (notifies families)")
    def cancel_enrollments(self, request, queryset):
        from .services import cancel

        cancelled = 0
        for enrollment in queryset.filter(status__in=Enrollment.ACTIVE_STATUSES):
            cancel(enrollment, Enrollment.CancelReason.ADMIN, actor=request.user)
            cancelled += 1
        self.message_user(
            request, f"Cancelled {cancelled} enrollment(s).", messages.WARNING
        )


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["session", "child", "present", "marked_by", "marked_at"]
    list_filter = ["present", "session__activity_class"]
