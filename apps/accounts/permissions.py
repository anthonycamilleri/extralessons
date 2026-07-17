from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Restrict a view to logged-in users with one of the given roles."""

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def parent_required(view_func):
    from .models import User

    return role_required(User.Role.PARENT)(view_func)


def provider_required(view_func):
    from .models import User

    return role_required(User.Role.PROVIDER)(view_func)


def admin_required(view_func):
    from .models import User

    return role_required(User.Role.ADMIN)(view_func)
