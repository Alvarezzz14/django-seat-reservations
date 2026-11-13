from django.urls import path

from . import views

urlpatterns = [
    path("activities/<int:activity_id>/", views.activity_detail, name="activity-detail"),
    path(
        "activities/<int:activity_id>/reservations/",
        views.reservation_create,
        name="reservation-create",
    ),
    path(
        "activities/<int:activity_id>/reservations/<int:reservation_id>/cancel/",
        views.reservation_cancel,
        name="reservation-cancel",
    ),
]
