"""The school's admin site: Django's admin, with the office desk on top.

Replaces ``django.contrib.admin`` in INSTALLED_APPS (see SchoolAdminConfig) so
that ``admin.site`` *is* this site and every ``@admin.register`` in the project
lands here unchanged.

Kept out of apps/dashboards/apps.py on purpose: AdminConfig inherits
``default = True``, and Django would otherwise pick it as the AppConfig for the
dashboards app itself.
"""
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.urls import path


class SchoolAdminSite(admin.AdminSite):
    site_title = "Activities admin"
    index_title = "Activities"

    def has_permission(self, request):
        """Admins and superusers only: a provider given staff status by
        mistake still cannot open the door."""
        from apps.accounts.models import User

        user = request.user
        return (
            user.is_active
            and user.is_staff
            and (user.role == User.Role.ADMIN or user.is_superuser)
        )

    def each_context(self, request):
        from apps.accounts.models import SiteConfig
        from apps.enrollments.models import Enrollment

        context = super().each_context(request)
        context["site_header"] = f"{SiteConfig.get().school_name} · Activities admin"
        # Also rendered on the login page and on error pages, where there is
        # nobody to count for.
        context["pending_requests_count"] = (
            Enrollment.objects.desk_count(request.user)
            if request.user.is_authenticated and self.has_permission(request)
            else None
        )
        return context

    def get_urls(self):
        """The help pages, ahead of the admin's own catch-all.

        Wrapped in admin_view so they are behind the same door as every other
        admin page, and namespaced with it, so templates link to them as
        ``{% url 'admin:help_index' %}``.
        """
        from apps.dashboards.help import views as help_views

        return [
            path("help/", self.admin_view(help_views.help_index), name="help_index"),
            path(
                "help/<slug:slug>/",
                self.admin_view(help_views.help_topic),
                name="help_topic",
            ),
        ] + super().get_urls()

    def index(self, request, extra_context=None):
        from apps.catalog.models import ActivityClass

        extra = {
            "classes_this_term": ActivityClass.objects.managed_by(request.user)
            .filter(term__is_active=True)
            .count(),
            **(extra_context or {}),
        }
        return super().index(request, extra)


class SchoolAdminConfig(AdminConfig):
    default_site = "apps.dashboards.admin_site.SchoolAdminSite"
