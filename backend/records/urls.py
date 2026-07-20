from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import EntryExitRecordViewSet

router = DefaultRouter()
router.register(r"", EntryExitRecordViewSet, basename="records")

urlpatterns = [
    path("", include(router.urls)),
]