"""The old /admin-tools/ dashboard, kept only as signposts.

Everything moved into the Django admin. Alert emails already in inboxes still
point here, so each old page forwards to its new home. Temporary redirects on
purpose: a 301 would be cached by browsers for good.
"""
from django.urls import path
from django.views.generic import RedirectView

urlpatterns = [
    path(
        "requests/",
        RedirectView.as_view(pattern_name="admin:enrollments_enrollment_requests"),
        name="admintools_requests",
    ),
    path(
        "classes/<int:object_id>/waitlist/",
        RedirectView.as_view(pattern_name="admin:catalog_activityclass_roster"),
        name="admintools_waitlist",
    ),
    path(
        "broadcast/",
        RedirectView.as_view(pattern_name="admin:notifications_broadcast_add"),
        name="admintools_broadcast",
    ),
]
