from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from access_management.models import (
    Department,
    Gate,
)
from accounts.models import User
from records.models import EntryExitRecord
from records.serializers import (
    EntryExitRecordSerializer,
)
from vehicles.models import Vehicle


class RecordSerializerRegressionTests(
    TestCase
):
    def setUp(self):
        from datetime import timedelta

        from access_management.models import (
            Department,
            Gate,
        )
        from accounts.models import User
        from records.models import (
            EntryExitRecord,
        )
        from vehicles.models import Vehicle

        today = timezone.now().date()

        self.department = (
            Department.objects.create(
                name=Department.Code.CSE,
                description=(
                    "Computer Science Engineering"
                ),
            )
        )

        self.gate = Gate.objects.create(
            name="Serializer Test Gate",
            location="Main Entrance",
            is_active=True,
        )

        self.user = User.objects.create_user(
            username="serializer_guard",
            password="StrongPassword123",
            first_name="Security",
            last_name="Guard",
            role=User.Role.SECURITY_GUARD,
        )

        self.vehicle = Vehicle.objects.create(
            owner_name="Rahul Nayak",
            owner_email="rahul@example.com",
            owner_phone="9876543210",
            owner_type=Vehicle.OwnerType.STUDENT,
            department=self.department,
            vehicle_company="Honda",
            vehicle_model="Activa",
            vehicle_type=(
                Vehicle.VehicleType.TWO_WHEELER
            ),
            color="White",
            fuel_type=Vehicle.FuelType.PETROL,
            registration_number="KA25AB1234",
            registration_date=today,
            valid_from=today,
            valid_until=(
                today + timedelta(days=365)
            ),
            authorization_status=(
                Vehicle
                .AuthorizationStatus
                .AUTHORIZED
            ),
        )

        self.record = (
            EntryExitRecord.objects.create(
                vehicle=self.vehicle,
                detected_plate_text=(
                    "KA25AB1234"
                ),
                direction=(
                    EntryExitRecord
                    .Direction
                    .ENTRY
                ),
                gate=self.gate,
                was_authorized=True,
                confidence_score=0.94,
                detection_source=(
                    EntryExitRecord
                    .DetectionSource
                    .WEBCAM
                ),
                recorded_by=self.user,
            )
        )

    def serialize(
        self,
        record=None,
    ):
        from records.serializers import (
            EntryExitRecordSerializer,
        )

        return EntryExitRecordSerializer(
            record or self.record
        ).data

    def test_serializer_includes_frontend_fields(
        self,
    ):
        data = self.serialize()

        self.assertEqual(
            data["gate_name"],
            str(self.gate),
        )

        self.assertEqual(
            data["recorded_by_name"],
            "Security Guard",
        )

        self.assertEqual(
            data["direction_display"],
            "Entry",
        )

        self.assertEqual(
            data[
                "detection_source_display"
            ],
            "Live Webcam",
        )

    def test_serializer_includes_vehicle_details(
        self,
    ):
        data = self.serialize()

        vehicle_data = data["vehicle_detail"]

        self.assertEqual(
            vehicle_data["owner_name"],
            "Rahul Nayak",
        )

        self.assertEqual(
            vehicle_data[
                "department_name"
            ],
            str(self.department),
        )

        self.assertEqual(
            vehicle_data[
                "vehicle_company"
            ],
            "Honda",
        )

        self.assertEqual(
            vehicle_data[
                "vehicle_model"
            ],
            "Activa",
        )

        self.assertEqual(
            vehicle_data["color"],
            "White",
        )

    def test_unknown_vehicle_serializes_safely(
        self,
    ):
        from records.models import (
            EntryExitRecord,
        )

        unknown_record = (
            EntryExitRecord.objects.create(
                vehicle=None,
                detected_plate_text=(
                    "KA01ZZ9999"
                ),
                direction=(
                    EntryExitRecord
                    .Direction
                    .ENTRY
                ),
                gate=None,
                was_authorized=False,
                confidence_score=0.70,
                detection_source=(
                    EntryExitRecord
                    .DetectionSource
                    .UPLOAD
                ),
                recorded_by=None,
            )
        )

        data = self.serialize(
            unknown_record
        )

        self.assertIsNone(
            data["vehicle_detail"]
        )

        self.assertIsNone(
            data["gate_name"]
        )

        self.assertIsNone(
            data["recorded_by_name"]
        )