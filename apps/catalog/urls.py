from django.urls import path

from . import views

urlpatterns = [
    path("", views.catalogue, name="catalogue"),
    path("classes/<int:term_id>/<slug:slug>/", views.class_detail, name="class_detail"),
]
