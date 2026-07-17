from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import ActivityClass, ClassSession, Provider, Term, generate_sessions


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_email", "contact_phone"]
    search_fields = ["name"]
    filter_horizontal = ["members"]


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date", "is_active"]
    list_filter = ["is_active"]


class CloneIntoTermForm(forms.Form):
    target_term = forms.ModelChoiceField(
        queryset=Term.objects.all(), label="Copy the selected classes into term"
    )


class ClassSessionInline(admin.TabularInline):
    model = ClassSession
    extra = 0


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
    list_filter = ["term", "status", "provider"]
    search_fields = ["title", "provider__name"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [ClassSessionInline]
    form = ActivityClassForm
    actions = ["publish_classes", "clone_into_term", "cancel_classes", "archive_classes"]

    def get_readonly_fields(self, request, obj=None):
        # Lifecycle changes must go through the actions (publish, cancel,
        # archive) so enrollments and notifications stay consistent — editing
        # the status directly would bypass cancel_class's bulk-cancel+notify.
        return ["status"] if obj else []

    @admin.action(description="Publish and generate sessions")
    def publish_classes(self, request, queryset):
        published = 0
        for cls in queryset.exclude(status=ActivityClass.Status.CANCELLED):
            cls.status = ActivityClass.Status.PUBLISHED
            cls.save(update_fields=["status"])
            generate_sessions(cls)
            published += 1
        self.message_user(request, f"Published {published} class(es) and generated sessions.")

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
    list_display = ["activity_class", "date", "cancelled"]
    list_filter = ["activity_class__term", "cancelled"]
    date_hierarchy = "date"
