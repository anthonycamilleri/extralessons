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
]
