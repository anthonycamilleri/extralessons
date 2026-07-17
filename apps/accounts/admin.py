from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

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
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "role", "password1", "password2")}),
    )


class GuardianInline(admin.TabularInline):
    model = Guardian
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "date_of_birth", "guardian_list"]
    search_fields = ["first_name", "last_name", "guardians__email"]
    inlines = [GuardianInline]

    @admin.display(description="Guardians")
    def guardian_list(self, obj):
        return ", ".join(str(g) for g in obj.guardians.all())


@admin.register(GuardianInvite)
class GuardianInviteAdmin(admin.ModelAdmin):
    list_display = ["email", "child", "invited_by", "created_at", "accepted_at"]
    readonly_fields = ["token"]


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Singleton: only editable, never added (the row is auto-created).
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
