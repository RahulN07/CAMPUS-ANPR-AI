from rest_framework import serializers

from .models import EntryExitRecord


class EntryExitRecordSerializer(
    serializers.ModelSerializer
):
    vehicle_detail = (
        serializers.SerializerMethodField()
    )

    gate_name = (
        serializers.SerializerMethodField()
    )

    recorded_by_name = (
        serializers.SerializerMethodField()
    )

    direction_display = serializers.CharField(
        source="get_direction_display",
        read_only=True,
    )

    detection_source_display = (
        serializers.CharField(
            source=(
                "get_detection_source_display"
            ),
            read_only=True,
        )
    )

    class Meta:
        model = EntryExitRecord

        fields = [
            "id",

            # Registered vehicle
            "vehicle",
            "vehicle_detail",

            # Detection information
            "detected_plate_text",
            "direction",
            "direction_display",
            "gate",
            "gate_name",
            "was_authorized",
            "confidence_score",
            "detection_source",
            "detection_source_display",

            # Evidence
            "captured_image",
            "plate_image",

            # Audit information
            "recorded_by",
            "recorded_by_name",
            "timestamp",
            "notes",
        ]

        read_only_fields = [
            "id",
            "timestamp",
            "vehicle_detail",
            "gate_name",
            "recorded_by_name",
            "direction_display",
            "detection_source_display",
        ]

    def get_vehicle_detail(
        self,
        obj,
    ):
        vehicle = obj.vehicle

        if vehicle is None:
            return None

        department = vehicle.department

        return {
            "id": vehicle.id,
            "registration_number": (
                vehicle.registration_number
            ),
            "owner_name": vehicle.owner_name,
            "owner_email": vehicle.owner_email,
            "owner_phone": vehicle.owner_phone,
            "owner_type": vehicle.owner_type,
            "owner_type_display": (
                vehicle.get_owner_type_display()
            ),
            "department": (
                department.id
                if department
                else None
            ),
            "department_name": (
                str(department)
                if department
                else None
            ),
            "vehicle_company": (
                vehicle.vehicle_company
            ),
            "vehicle_model": (
                vehicle.vehicle_model
            ),
            "vehicle_type": (
                vehicle.vehicle_type
            ),
            "vehicle_type_display": (
                vehicle.get_vehicle_type_display()
            ),
            "color": vehicle.color,
            "fuel_type": vehicle.fuel_type,
            "fuel_type_display": (
                vehicle.get_fuel_type_display()
            ),
            "authorization_status": (
                vehicle.authorization_status
            ),
        }

    def get_gate_name(
        self,
        obj,
    ):
        if obj.gate is None:
            return None

        return str(obj.gate)

    def get_recorded_by_name(
        self,
        obj,
    ):
        user = obj.recorded_by

        if user is None:
            return None

        full_name = user.get_full_name().strip()

        return (
            full_name
            or user.username
        )