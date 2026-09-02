"""Where to send someone after they log in, sign up or finish a form.

A `next` parameter is how the register flow keeps its place: a parent who
lands on a class page, creates an account and adds a child should arrive back
at that class's register form, not on a dashboard. Only same-host paths are
honoured, so a crafted link cannot bounce a fresh login to another site.
"""
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, default=None):
    """The request's `next` (POST first, then GET) if it points at this site."""
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return default
