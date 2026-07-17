from django.contrib import admin
from django.utils import timezone

from .models import Broadcast, Notification, NotificationTemplate


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ["event", "enabled", "email_subject", "wa_template_name"]
    list_filter = ["enabled"]


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ["subject", "sender", "scope", "created_at", "sent_at"]
    filter_horizontal = ["classes"]
    readonly_fields = ["sent_at"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "event", "recipient", "channel", "status", "attempts"]
    list_filter = ["status", "channel", "event"]
    search_fields = ["recipient__email"]
    readonly_fields = [f.name for f in Notification._meta.fields]
    actions = ["retry_failed"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Retry failed notifications")
    def retry_failed(self, request, queryset):
        updated = queryset.filter(status=Notification.Status.FAILED).update(
            status=Notification.Status.PENDING,
            attempts=0,
            next_attempt_at=timezone.now(),
            last_error="",
        )
        self.message_user(request, f"Requeued {updated} notification(s).")
