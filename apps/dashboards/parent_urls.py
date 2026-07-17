from django.urls import path

from . import parent_views

urlpatterns = [
    path("", parent_views.home, name="parent_home"),
    path("profile/", parent_views.profile, name="parent_profile"),
    path("children/add/", parent_views.child_add, name="child_add"),
    path("children/<int:child_id>/edit/", parent_views.child_edit, name="child_edit"),
    path(
        "children/<int:child_id>/invite-guardian/",
        parent_views.child_invite_guardian,
        name="child_invite_guardian",
    ),
    path("enroll/<int:class_id>/", parent_views.enroll, name="enroll"),
    path(
        "enrollments/<int:enrollment_id>/cancel/",
        parent_views.enrollment_cancel,
        name="enrollment_cancel",
    ),
    path(
        "offers/<int:enrollment_id>/confirm/",
        parent_views.offer_confirm,
        name="offer_confirm",
    ),
    path(
        "offers/<int:enrollment_id>/decline/",
        parent_views.offer_decline,
        name="offer_decline",
    ),
]
