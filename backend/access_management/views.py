import ipaddress

from django.db import transaction
from rest_framework import generics, permissions

from accounts.models import AuditLog
from .models import (
    Department,
    Gate,
    RolePermission,
)
from .serializers import (
    DepartmentSerializer,
    GateSerializer,
)


def get_request_ip(request):
    """
    Return a validated client IP for audit logging.

    REMOTE_ADDR is used instead of trusting forwarded headers
    until trusted-proxy handling is configured in production.
    """

    raw_ip = request.META.get("REMOTE_ADDR")

    if not raw_ip:
        return None

    try:
        return str(ipaddress.ip_address(raw_ip))
    except ValueError:
        return None


class CanManageGateConfiguration(
    permissions.BasePermission
):
    """
    All authenticated users can read gates.

    Changes require an admin, superuser, or a role with
    can_manage_settings enabled.
    """

    message = (
        "You do not have permission to manage "
        "gate configuration."
    )

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS:
            return True

        if user.is_superuser or user.is_admin:
            return True

        return RolePermission.objects.filter(
            role=user.role,
            can_manage_settings=True,
        ).exists()


class DepartmentListView(generics.ListAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Department.objects.all().order_by("id")


class GateListView(generics.ListCreateAPIView):
    serializer_class = GateSerializer
    permission_classes = [
        CanManageGateConfiguration,
    ]
    pagination_class = None

    def get_queryset(self):
        return Gate.objects.all().order_by("id")

    @transaction.atomic
    def perform_create(self, serializer):
        gate = serializer.save()

        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.CREATE,
            model_name="Gate",
            object_id=str(gate.pk),
            description=(
                f"Created gate configuration: {gate.name}"
            ),
            ip_address=get_request_ip(self.request),
        )


class GateDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = GateSerializer
    permission_classes = [
        CanManageGateConfiguration,
    ]
    queryset = Gate.objects.all()

    @transaction.atomic
    def perform_update(self, serializer):
        gate = serializer.save()

        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            model_name="Gate",
            object_id=str(gate.pk),
            description=(
                f"Updated gate configuration: {gate.name}"
            ),
            ip_address=get_request_ip(self.request),
        )

    @transaction.atomic
    def perform_destroy(self, instance):
        gate_id = str(instance.pk)
        gate_name = instance.name

        instance.delete()

        AuditLog.objects.create(
            user=self.request.user,
            action=AuditLog.Action.DELETE,
            model_name="Gate",
            object_id=gate_id,
            description=(
                f"Deleted gate configuration: {gate_name}"
            ),
            ip_address=get_request_ip(self.request),
        )