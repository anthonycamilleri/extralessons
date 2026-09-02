from django.contrib import admin
from django.template.defaultfilters import filesizeformat
from django.utils.html import format_html

from .models import StoredFile


@admin.register(StoredFile)
class StoredFileAdmin(admin.ModelAdmin):
    """Read-only inventory of what the database is holding for MEDIA_URL."""

    list_display = ("name", "content_type", "human_size", "created_at")
    search_fields = ("name",)
    readonly_fields = ("name", "content_type", "human_size", "created_at", "preview")
    exclude = ("content", "size")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="size", ordering="size")
    def human_size(self, obj):
        return filesizeformat(obj.size)

    @admin.display(description="preview")
    def preview(self, obj):
        if not obj.content_type.startswith("image/"):
            return "—"
        from .storage import DatabaseStorage

        return format_html(
            '<img src="{}" alt="" style="max-width: 480px; max-height: 320px">',
            DatabaseStorage().url(obj.name),
        )
