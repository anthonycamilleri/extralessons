from apps.accounts.models import User


def admin_badge(request):
    """The site nav's pending-requests count, for logged-in admins only.

    One COUNT per page for admins; nothing at all for everyone else.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.role != User.Role.ADMIN:
        return {}
    from apps.enrollments.models import Enrollment

    return {"pending_requests_count": Enrollment.objects.pending_for(user).count()}
