from django.urls import path

from . import admintools_views

urlpatterns = [
    path("requests/", admintools_views.requests_queue, name="admintools_requests"),
    path(
        "requests/<int:enrollment_id>/approve/",
        admintools_views.request_approve,
        name="admintools_request_approve",
    ),
    path(
        "requests/<int:enrollment_id>/reject/",
        admintools_views.request_reject,
        name="admintools_request_reject",
    ),
    path(
        "classes/<int:class_id>/waitlist/",
        admintools_views.waitlist,
        name="admintools_waitlist",
    ),
    path(
        "waitlist/<int:enrollment_id>/offer/",
        admintools_views.waitlist_offer,
        name="admintools_waitlist_offer",
    ),
]
