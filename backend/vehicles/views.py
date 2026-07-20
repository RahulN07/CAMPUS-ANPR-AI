from rest_framework import generics, permissions
from rest_framework.filters import OrderingFilter, SearchFilter

from access_management.models import Department

from .models import Vehicle, VehicleCompany, VehicleModel
from .serializers import (
    DepartmentSerializer,
    VehicleCompanySerializer,
    VehicleModelSerializer,
    VehicleSerializer,
)


class VehicleListCreateView(generics.ListCreateAPIView):
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = [
        "registration_number",
        "owner_name",
        "owner_email",
        "owner_phone",
        "vehicle_company",
        "vehicle_model",
    ]
    ordering_fields = [
        "created_at",
        "registration_number",
        "owner_name",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = Vehicle.objects.select_related("department").all()

        authorization_status = self.request.query_params.get(
            "authorization_status"
        )
        vehicle_type = self.request.query_params.get("vehicle_type")
        owner_type = self.request.query_params.get("owner_type")
        department = self.request.query_params.get("department")

        if authorization_status:
            queryset = queryset.filter(
                authorization_status=authorization_status
            )

        if vehicle_type:
            queryset = queryset.filter(vehicle_type=vehicle_type)

        if owner_type:
            queryset = queryset.filter(owner_type=owner_type)

        if department:
            queryset = queryset.filter(department_id=department)

        return queryset


class VehicleDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Vehicle.objects.select_related("department").all()
    serializer_class = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]


class VehicleCompanyListView(generics.ListAPIView):
    serializer_class = VehicleCompanySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        queryset = VehicleCompany.objects.filter(is_active=True)

        vehicle_type = self.request.query_params.get("vehicle_type")

        if vehicle_type:
            queryset = queryset.filter(vehicle_type=vehicle_type)

        return queryset.order_by("name")


class VehicleModelListView(generics.ListAPIView):
    serializer_class = VehicleModelSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        queryset = VehicleModel.objects.filter(
            is_active=True,
            company__is_active=True,
        ).select_related("company")

        company = self.request.query_params.get("company")

        if company:
            queryset = queryset.filter(company_id=company)

        return queryset.order_by("name")


class DepartmentListView(generics.ListAPIView):
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Department.objects.all().order_by("id")