"""Who may do what in the Django admin, without per-user permission ticking.

Three kinds of admin use the same admin site:

* a **super admin** (admin role plus superuser status) has Django's usual
  run of the place;
* a **regular admin** (admin role, not a superuser) gets only the class-bound
  controls, and only for the classes assigned to them. Which controls is
  declared per ModelAdmin with ``school_admin_can``; which rows is the job of
  ``apps.catalog.admin.ScopedByClassMixin``;
* a **read-only admin** (its own role, ``User.Role.READONLY_ADMIN``) sees
  every row of every model and may change none of them. Nothing here grants
  that: ``User.has_perm`` answers every ``view_*`` permission with yes for
  the role and everything else with no, so Django's own checks do the work on
  every ModelAdmin, mixin or not. What the mixin adds below is for the
  regular admin role only, and the custom actions declare the verb they need
  (``@admin.action(permissions=...)``) so the read-only role never sees them.

Everything structural (school years, terms, providers, user accounts, site
configuration, templates) simply has no mixin and stays with the superusers,
read-only admins looking on.
"""
from apps.accounts.models import User


def is_school_admin(request):
    """An active account with the (acting) admin role: the one the mixin
    below hands verbs to. Superusers pass Django's own checks, and read-only
    admins are deliberately not included: they hold view permissions only."""
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
