from rest_framework import serializers

from access_management.models import Department
from .models import Vehicle, VehicleCompany, VehicleModel


class VehicleCompanySerializer(serializers.ModelSerializer):
    vehicle_type_display = serializers.CharField(
        source="get_vehicle_type_display",
        read_only=True,
    )

    class Meta:
        model = VehicleCompany
        fields = [
            "id",
            "name",
            "vehicle_type",
            "vehicle_type_display",
            "is_active",
        ]


class VehicleModelSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(
        source="company.name",
        read_only=True,
    )

    class Meta:
        model = VehicleModel
        fields = [
            "id",
            "name",
            "company",
            "company_name",
            "is_active",
        ]


class DepartmentSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(
        source="__str__",
        read_only=True,
    )

    class Meta:
        model = Department
        fields = [
            "id",
            "display_name",
        ]


class VehicleSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source="department.__str__",
        read_only=True,
    )

    owner_type_display = serializers.CharField(
        source="get_owner_type_display",
        read_only=True,
    )

    vehicle_type_display = serializers.CharField(
        source="get_vehicle_type_display",
        read_only=True,
    )

    fuel_type_display = serializers.CharField(
        source="get_fuel_type_display",
        read_only=True,
    )

    authorization_status_display = serializers.CharField(
        source="get_authorization_status_display",
        read_only=True,
    )

    class Meta:
        model = Vehicle
        fields = "__all__"

    def validate(self, attrs):
        valid_from = attrs.get(
            "valid_from",
            getattr(self.instance, "valid_from", None),
        )
        valid_until = attrs.get(
            "valid_until",
            getattr(self.instance, "valid_until", None),
        )

        if valid_from and valid_until and valid_until < valid_from:
            raise serializers.ValidationError(
                {
                    "valid_until": (
                        "Valid until date cannot be earlier than valid from date."
                    )
                }
            )

        return attrs

    def validate_registration_number(self, value):
        return (
            value.upper()
            .replace(" ", "")
            .replace("-", "")
        )