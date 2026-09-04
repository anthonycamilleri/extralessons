from django import forms
from django.contrib import admin
from django.db.models import Q
from django.utils import timezone

from apps.accounts.admin_permissions import SchoolAdminPermissionMixin
from apps.catalog.models import ActivityClass

from . import services
from .models import Broadcast, Notification, NotificationTemplate


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ["event", "enabled", "email_subject", "wa_template_name"]
    list_filter = ["enabled"]


class BroadcastAdminForm(forms.ModelForm):
    """The announcement composer. "All classes" means every class on this
    admin's desk: all published classes for a super admin, only their own
    for anyone else; that narrowing is what audience() records."""

    request = None  # injected per request by BroadcastAdmin.get_form

    class Meta:
        model = Broadcast
        fields = ["scope", "classes", "subject", "body"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.request.user
        self.managed = ActivityClass.objects.managed_by(user)
        self.narrowed = not user.is_super_admin
        # The read-only view of a sent announcement builds this form with no
        # editable fields at all; only dress the ones that are there.
        if "scope" in self.fields:
            scope = self.fields["scope"]
            scope.label = "Audience"
            scope.initial = Broadcast.Scope.ALL_CLASSES
            # Swapping the widget drops the choices it was given: hand them
            # over again, minus the empty "---------" a radio has no use for.
            scope.widget = forms.RadioSelect()
            if self.narrowed:
                scope.choices = [
                    (Broadcast.Scope.ALL_CLASSES, "All my classes"),
                    (Broadcast.Scope.SELECTED_CLASSES, "Selected classes"),
                ]
            else:
                scope.choices = [choice for choice in Broadcast.Scope.choices]
        if "classes" in self.fields:
            classes = self.fields["classes"]
            classes.widget = forms.CheckboxSelectMultiple()
            classes.queryset = self.managed.filter(term__is_active=True).order_by("title")
            classes.required = False
            classes.label = "Classes (when audience is 'Selected classes')"
            classes.help_text = ""
        if "body" in self.fields:
            self.fields["body"].label = "Message"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("scope") == Broadcast.Scope.SELECTED_CLASSES and not cleaned.get(
            "classes"
        ):
            self.add_error("classes", "Pick at least one class.")
        return cleaned

    def audience(self):
        """(scope, classes) to hand to create_broadcast.

        A narrowed "all classes" is stored as an explicit selection of the
        admin's published classes, so the Broadcast row records exactly who
        was addressed and the fan-out never reaches families outside their
        remit.
        """
        scope = self.cleaned_data["scope"]
        classes = self.cleaned_data["classes"]
        if scope == Broadcast.Scope.ALL_CLASSES and self.narrowed:
            scope = Broadcast.Scope.SELECTED_CLASSES
            classes = self.managed.published()
        return scope, classes


@admin.register(Broadcast)
class BroadcastAdmin(SchoolAdminPermissionMixin, admin.ModelAdmin):
    """Add = send an announcement; existing rows are the sent history."""

    school_admin_can = frozenset({"view", "add"})
    form = BroadcastAdminForm
    list_display = ["subject", "sender", "scope", "created_at", "sent_at", "recipients"]
    list_filter = ["scope"]

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("sender")
        if request.user.is_superuser:
            return qs
        managed = ActivityClass.objects.managed_by(request.user)
        return qs.filter(Q(sender=request.user) | Q(classes__in=managed)).distinct()

    def get_form(self, request, obj=None, change=False, **kwargs):
        # get_form builds a fresh class per call, so this is per-request state.
        form_class = super().get_form(request, obj, change=change, **kwargs)
        form_class.request = request
        return form_class

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["scope", "classes", "subject", "body"]
        return ["sender", "scope", "classes", "subject", "body", "created_at", "sent_at", "recipients"]

    def get_readonly_fields(self, request, obj=None):
        return [] if obj is None else self.get_fields(request, obj)

    def has_change_permission(self, request, obj=None):
        # What was sent cannot be edited; the change page is the record.
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="recipients")
    def recipients(self, obj):
        return obj.notifications.count()

    def save_model(self, request, obj, form, change):
        scope, classes = form.audience()
        broadcast, count = services.create_broadcast(
            sender=request.user,
            scope=scope,
            subject=obj.subject,
            body=obj.body,
            classes=classes,
        )
        # The service saved the row; let the admin's logging and redirect see it.
        for field in ("pk", "id", "sender", "scope", "created_at", "sent_at"):
            setattr(obj, field, getattr(broadcast, field))
        obj._state.adding = False
        obj._state.db = broadcast._state.db
        self.message_user(
            request, f"Announcement queued for {services.family_count_phrase(count)}."
        )

    def save_related(self, request, form, formsets, change):
        # create_broadcast already set the classes; form.save_m2m() would
        # overwrite a narrowed "all my classes" with the empty selection.
        pass


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "event", "recipient", "channel", "status", "attempts"]
    list_filter = ["status", "channel", "event"]
    # recipient is empty for address-only sends (invites, contact-form
    # messages), so the address snapshots have to be searchable too.
    search_fields = ["recipient__email", "recipient_email", "reply_to"]
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
