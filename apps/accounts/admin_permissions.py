"""Who may do what in the Django admin, without per-user permission ticking.

Two kinds of admin use the same admin site:

* a **super admin** (admin role plus superuser status) has Django's usual
  run of the place;
* a **regular admin** (admin role, not a superuser) gets only the class-bound
  controls, and only for the classes assigned to them. Which controls is
  declared per ModelAdmin with ``school_admin_can``; which rows is the job of
  ``apps.catalog.admin.ScopedByClassMixin``.

Everything structural (school years, terms, providers, user accounts, site
configuration, templates) simply has no mixin and stays with the superusers.
"""
from apps.accounts.models import User


def is_school_admin(request):
    """An active admin-role account. Superusers pass Django's own checks."""
    user = request.user
    return (
        user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.role == User.Role.ADMIN
    )


class SchoolAdminPermissionMixin:
    """Grant regular admins the verbs in ``school_admin_can``; defer otherwise.

    Put it first in the bases. A ModelAdmin's own ``has_add_permission``
    override still wins, because it is defined on the class itself.
    """

    school_admin_can = frozenset({"view", "change"})

    def _school_admin_may(self, request, verb):
        return verb in self.school_admin_can and is_school_admin(request)

    def has_module_permission(self, request):
        return is_school_admin(request) or super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._school_admin_may(request, "view") or super().has_view_permission(
            request, obj
        )

    def has_add_permission(self, request):
        return self._school_admin_may(request, "add") or super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._school_admin_may(request, "change") or super().has_change_permission(
            request, obj
        )

    def has_delete_permission(self, request, obj=None):
        return self._school_admin_may(request, "delete") or super().has_delete_permission(
            request, obj
        )
