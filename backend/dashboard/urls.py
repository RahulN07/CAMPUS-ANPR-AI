from django.urls import path
from .views import dashboard_summary

urlpatterns = [
    path("", dashboard_summary, name="dashboard-summary"),
]