from django.utils.dateparse import parse_date
from django_filters.rest_framework import (
    DjangoFilterBackend,
)
from rest_framework import viewsets
from rest_framework.filters import (
    OrderingFilter,
    SearchFilter,
)
from rest_framework.pagination import (
    PageNumberPagination,
)
from rest_framework.permissions import (
    IsAuthenticated,
)

from accounts.permissions import (
    IsAdmin,
    IsAdminOrSecurity,
)

from .models import EntryExitRecord
from .serializers import (
    EntryExitRecordSerializer,
)


class RecordPagination(
    PageNumberPagination
):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 500


class EntryExitRecordViewSet(
    viewsets.ModelViewSet
):
    serializer_class = (
        EntryExitRecordSerializer
    )

    pagination_class = RecordPagination

    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]

    filterset_fields = [
        "direction",
        "gate",
        "was_authorized",
        "detection_source",
        "vehicle",
    ]

    search_fields = [
        "detected_plate_text",
        "vehicle__registration_number",
        "vehicle__owner_name",
        "vehicle__owner_email",
        "vehicle__owner_phone",
        "gate__name",
    ]

    ordering_fields = [
        "timestamp",
        "confidence_score",
        "detected_plate_text",
        "direction",
    ]

    ordering = [
        "-timestamp",
    ]

    def get_queryset(self):
        queryset = (
            EntryExitRecord.objects
            .select_related(
                "vehicle",
                "vehicle__department",
                "gate",
                "recorded_by",
            )
            .all()
        )

        date_from = parse_date(
            self.request.query_params.get(
                "date_from",
                "",
            )
        )

        date_to = parse_date(
            self.request.query_params.get(
                "date_to",
                "",
            )
        )

        if date_from:
            queryset = queryset.filter(
                timestamp__date__gte=date_from
            )

        if date_to:
            queryset = queryset.filter(
                timestamp__date__lte=date_to
            )

        return queryset

    def get_permissions(self):
        """
        Permission policy:

        List and retrieve:
            Any authenticated user.

        Create:
            Admin or Security Guard.

        Update and delete:
            Admin only.
        """

        if self.action == "create":
            permission_classes = [
                IsAuthenticated,
                IsAdminOrSecurity,
            ]

        elif self.action in {
            "update",
            "partial_update",
            "destroy",
        }:
            permission_classes = [
                IsAuthenticated,
                IsAdmin,
            ]

        else:
            permission_classes = [
                IsAuthenticated,
            ]

        return [
            permission()
            for permission
            in permission_classes
        ]

    def perform_create(
        self,
        serializer,
    ):
        """
        Always use the authenticated user as recorded_by.

        This prevents a client from submitting another user's ID.
        """

        serializer.save(
            recorded_by=self.request.user
        )