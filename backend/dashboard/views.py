from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from vehicles.models import Vehicle
from records.models import EntryExitRecord
from notifications.models import Notification
from accounts.models import User


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """
    Returns summary statistics for the dashboard.
    """
    today = timezone.now().date()

    data = {
        "total_vehicles": Vehicle.objects.count(),

        "authorized_vehicles": Vehicle.objects.filter(
            authorization_status="AUTHORIZED"
        ).count(),

        "unauthorized_vehicles": Vehicle.objects.filter(
            authorization_status="UNAUTHORIZED"
        ).count(),

        "today_entries": EntryExitRecord.objects.filter(
            direction="ENTRY",
            timestamp__date=today
        ).count(),

        "today_exits": EntryExitRecord.objects.filter(
            direction="EXIT",
            timestamp__date=today
        ).count(),

        "active_users": User.objects.filter(
            is_active=True
        ).count(),

        "unread_notifications": Notification.objects.filter(
            is_read=False
        ).count(),

        "recent_records": list(
            EntryExitRecord.objects.order_by("-timestamp").values(
                "id",
                "detected_plate_text",
                "direction",
                "was_authorized",
                "confidence_score",
                "timestamp"
            )[:5]
        )
    }

    return Response(data)