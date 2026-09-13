from apps.accounts.models import User


def admin_badge(request):
    """The site nav's requests count (new requests plus cancellation requests),
    for logged-in admins only.

    Two COUNTs per page for admins; nothing at all for everyone else.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.role != User.Role.ADMIN:
        return {}
    from apps.enrollments.models import Enrollment

    return {"pending_requests_count": Enrollment.objects.desk_count(user)}


def provider_nav(request):
    """Which provider-side links the navigation shows: *Instructors* for an
    account that runs a provider, *My profile* for one with an instructor
    profile. Two EXISTS queries per page for providers; nothing for anyone else.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.role != User.Role.PROVIDER:
        return {}
    from apps.catalog.models import Instructor, Provider

    return {
        "provider_manages": Provider.objects.managed_by(user).exists(),
        "provider_teaches": Instructor.objects.for_user(user).exists(),
    }
