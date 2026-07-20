from django.urls import path

from .views import (
    DepartmentListView,
    VehicleCompanyListView,
    VehicleDetailView,
    VehicleListCreateView,
    VehicleModelListView,
)

urlpatterns = [
    path(
        "companies/",
        VehicleCompanyListView.as_view(),
        name="vehicle-company-list",
    ),
    path(
        "models/",
        VehicleModelListView.as_view(),
        name="vehicle-model-list",
    ),
    path(
        "departments/",
        DepartmentListView.as_view(),
        name="department-list",
    ),

    path("", VehicleListCreateView.as_view(), name="vehicle-list-create"),
    path(
        "<int:pk>/",
        VehicleDetailView.as_view(),
        name="vehicle-detail",
    ),
]