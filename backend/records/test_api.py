from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from access_management.models import Gate
from accounts.models import User
from records.models import EntryExitRecord


class EntryExitRecordAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.admin = User.objects.create_user(
            username="records_admin",
            password="StrongPassword123",
            role=User.Role.ADMIN,
        )

        self.guard = User.objects.create_user(
            username="records_guard",
            password="StrongPassword123",
            role=User.Role.SECURITY_GUARD,
        )

        self.viewer = User.objects.create_user(
            username="records_viewer",
            password="StrongPassword123",
            role=User.Role.VIEWER,
        )

        self.gate = Gate.objects.create(
            name="Records Test Gate",
            location="Test Location",
            is_active=True,
        )

        self.record = self.create_record(
            plate="KA25AB1000"
        )

    def create_record(
        self,
        plate,
        direction=EntryExitRecord.Direction.ENTRY,
        recorded_by=None,
    ):
        return EntryExitRecord.objects.create(
            detected_plate_text=plate,
            direction=direction,
            gate=self.gate,
            was_authorized=False,
            confidence_score=0.85,
            detection_source=(
                EntryExitRecord
                .DetectionSource
                .WEBCAM
            ),
            recorded_by=(
                recorded_by or self.guard
            ),
        )

    def records_url(self):
        return reverse("records-list")

    def record_detail_url(self, record_id):
        return reverse(
            "records-detail",
            kwargs={"pk": record_id},
        )

    def test_authenticated_viewer_can_list_records(
        self,
    ):
        self.client.force_authenticate(
            user=self.viewer
        )

        response = self.client.get(
            self.records_url()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertIn(
            "results",
            response.data,
        )

    def test_page_size_parameter_is_supported(
        self,
    ):
        for number in range(1, 15):
            self.create_record(
                plate=f"KA25AB{number:04d}"
            )

        self.client.force_authenticate(
            user=self.viewer
        )

        response = self.client.get(
            self.records_url(),
            {"page_size": 12},
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["count"],
            15,
        )

        self.assertEqual(
            len(response.data["results"]),
            12,
        )

    def test_date_from_filter_excludes_old_records(
        self,
    ):
        old_record = self.create_record(
            plate="KA25AB2000"
        )

        EntryExitRecord.objects.filter(
            id=old_record.id
        ).update(
            timestamp=(
                timezone.now()
                - timedelta(days=1)
            )
        )

        self.client.force_authenticate(
            user=self.viewer
        )

        response = self.client.get(
            self.records_url(),
            {
                "date_from": (
                    timezone.now()
                    .date()
                    .isoformat()
                ),
                "page_size": 100,
            },
        )

        returned_ids = {
            item["id"]
            for item in response.data["results"]
        }

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertIn(
            self.record.id,
            returned_ids,
        )

        self.assertNotIn(
            old_record.id,
            returned_ids,
        )

    def test_viewer_cannot_create_record(
        self,
    ):
        self.client.force_authenticate(
            user=self.viewer
        )

        response = self.client.post(
            self.records_url(),
            {
                "detected_plate_text": (
                    "KA25AB3000"
                ),
                "direction": "ENTRY",
                "gate": self.gate.id,
                "was_authorized": False,
                "confidence_score": 0.80,
                "detection_source": "MANUAL",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            EntryExitRecord.objects.filter(
                detected_plate_text=(
                    "KA25AB3000"
                )
            ).exists()
        )

    def test_security_guard_can_create_record(
        self,
    ):
        self.client.force_authenticate(
            user=self.guard
        )

        response = self.client.post(
            self.records_url(),
            {
                "detected_plate_text": (
                    "KA25AB4000"
                ),
                "direction": "ENTRY",
                "gate": self.gate.id,
                "was_authorized": False,
                "confidence_score": 0.82,
                "detection_source": "MANUAL",

                # The API must ignore this
                # attempted user impersonation.
                "recorded_by": self.viewer.id,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            201,
        )

        created_record = (
            EntryExitRecord.objects.get(
                detected_plate_text=(
                    "KA25AB4000"
                )
            )
        )

        self.assertEqual(
            created_record.recorded_by,
            self.guard,
        )

    def test_only_admin_can_delete_record(
        self,
    ):
        url = self.record_detail_url(
            self.record.id
        )

        self.client.force_authenticate(
            user=self.guard
        )

        guard_response = (
            self.client.delete(url)
        )

        self.assertEqual(
            guard_response.status_code,
            403,
        )

        self.assertTrue(
            EntryExitRecord.objects.filter(
                id=self.record.id
            ).exists()
        )

        self.client.force_authenticate(
            user=self.admin
        )

        admin_response = (
            self.client.delete(url)
        )

        self.assertEqual(
            admin_response.status_code,
            204,
        )

        self.assertFalse(
            EntryExitRecord.objects.filter(
                id=self.record.id
            ).exists()
        )