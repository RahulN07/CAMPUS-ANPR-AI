from django.urls import path

from .live_views import GateLiveFrameView
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
    path(
        "gates/<int:gate_id>/live-frame/",
        GateLiveFrameView.as_view(),
        name="gate-live-frame",
    ),
]