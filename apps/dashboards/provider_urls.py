from django.urls import path

from . import provider_views

urlpatterns = [
    path("", provider_views.home, name="provider_home"),
    path("classes/<int:class_id>/", provider_views.class_detail, name="provider_class"),
    path(
        "classes/<int:class_id>/sessions/<int:session_id>/attendance/",
        provider_views.attendance,
        name="provider_attendance",
    ),
    path("broadcast/", provider_views.broadcast, name="provider_broadcast"),
    # The guides for providers and instructors (apps/dashboards/help).
    path("help/", provider_views.help_index, name="provider_help_index"),
    path("help/<slug:slug>/", provider_views.help_topic, name="provider_help_topic"),
    # Instructors: managed by the provider's own accounts.
    path("instructors/", provider_views.instructors, name="provider_instructors"),
    path(
        "instructors/add/<int:provider_id>/",
        provider_views.instructor_add,
        name="provider_instructor_add",
    ),
    path(
        "instructors/add-self/<int:provider_id>/",
        provider_views.instructor_add_self,
        name="provider_instructor_add_self",
    ),
    path(
        "instructors/<int:instructor_id>/",
        provider_views.instructor_detail,
        name="provider_instructor",
    ),
    path(
        "instructors/<int:instructor_id>/resend-invite/",
        provider_views.instructor_resend_invite,
        name="provider_instructor_resend",
    ),
    path(
        "instructors/<int:instructor_id>/remove/",
        provider_views.instructor_remove,
        name="provider_instructor_remove",
    ),
    # The profile: the instructor's own page, also open to their manager.
    path("profile/", provider_views.my_profile, name="provider_my_profile"),
    path(
        "instructors/<int:instructor_id>/profile/",
        provider_views.instructor_profile,
        name="provider_instructor_profile",
    ),
    path(
        "instructors/<int:instructor_id>/certificate/",
        provider_views.instructor_certificate,
        name="provider_instructor_certificate",
    ),
]
