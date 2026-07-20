from django.urls import path

from .views import (
    DetectPlateView,
    RecentDetectionsView,
)

urlpatterns = [
    path(
        "detect/",
        DetectPlateView.as_view(),
        name="detect-plate",
    ),
    path(
        "recent-detections/",
        RecentDetectionsView.as_view(),
        name="recent-detections",
    ),
]