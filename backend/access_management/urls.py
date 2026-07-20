from django.urls import path

from .views import (
    DepartmentListView,
    GateDetailView,
    GateListView,
)


urlpatterns = [
    path(
        "departments/",
        DepartmentListView.as_view(),
        name="department-list",
    ),
    path(
        "gates/",
        GateListView.as_view(),
        name="gate-list",
    ),
    path(
        "gates/<int:pk>/",
        GateDetailView.as_view(),
        name="gate-detail",
    ),
]