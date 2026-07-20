from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import AuditLog, User
from access_management.models import Gate, RolePermission
from access_management.serializers import GateSerializer


class GateConfigurationTests(TestCase):
    def test_gate_uses_safe_capture_defaults(self):
        gate = Gate(name="Main Entry Gate")

        self.assertEqual(
            gate.gate_type,
            Gate.GateType.ENTRY,
        )
        self.assertEqual(
            gate.camera_source,
            Gate.CameraSource.WEBCAM,
        )
        self.assertEqual(gate.camera_device_index, 0)
        self.assertEqual(gate.target_fps, 10)
        self.assertTrue(gate.line_crossing_enabled)

    def test_entry_and_exit_gate_types_are_explicit(self):
        entry_gate = Gate.objects.create(
            name="Entry Gate",
            gate_type=Gate.GateType.ENTRY,
        )

        exit_gate = Gate.objects.create(
            name="Exit Gate",
            gate_type=Gate.GateType.EXIT,
        )

        self.assertEqual(
            entry_gate.gate_type,
            Gate.GateType.ENTRY,
        )
        self.assertEqual(
            exit_gate.gate_type,
            Gate.GateType.EXIT,
        )
        self.assertEqual(
            entry_gate.get_gate_type_display(),
            "Entry Gate",
        )
        self.assertEqual(
            exit_gate.get_gate_type_display(),
            "Exit Gate",
        )

    def test_all_required_camera_sources_are_supported(self):
        supported_sources = {
            value
            for value, _label in Gate.CameraSource.choices
        }

        self.assertEqual(
            supported_sources,
            {
                Gate.CameraSource.WEBCAM,
                Gate.CameraSource.USB_CAMERA,
                Gate.CameraSource.IP_CAMERA,
                Gate.CameraSource.RTSP,
                Gate.CameraSource.CCTV,
                Gate.CameraSource.VIDEO_UPLOAD,
            },
        )

    def test_default_line_coordinates_are_normalized(self):
        gate = Gate(name="Normalized Line Gate")

        coordinates = (
            gate.line_start_x,
            gate.line_start_y,
            gate.line_end_x,
            gate.line_end_y,
        )

        for coordinate in coordinates:
            self.assertGreaterEqual(coordinate, 0.0)
            self.assertLessEqual(coordinate, 1.0)

        self.assertNotEqual(
            (gate.line_start_x, gate.line_start_y),
            (gate.line_end_x, gate.line_end_y),
        )

    def test_identical_line_points_are_rejected(self):
        gate = Gate(
            name="Invalid Line Gate",
            line_crossing_enabled=True,
            line_start_x=0.5,
            line_start_y=0.5,
            line_end_x=0.5,
            line_end_y=0.5,
        )

        with self.assertRaises(ValidationError) as context:
            gate.full_clean()

        self.assertIn(
            "line_end_x",
            context.exception.message_dict,
        )
        self.assertIn(
            "line_end_y",
            context.exception.message_dict,
        )

    def test_identical_points_allowed_when_line_disabled(self):
        gate = Gate(
            name="Disabled Line Gate",
            line_crossing_enabled=False,
            line_start_x=0.5,
            line_start_y=0.5,
            line_end_x=0.5,
            line_end_y=0.5,
        )

        gate.full_clean()

    def test_coordinate_and_fps_limits_are_validated(self):
        invalid_cases = (
            {"line_start_x": -0.01},
            {"line_start_y": 1.01},
            {"line_end_x": 1.01},
            {"line_end_y": -0.01},
            {"target_fps": 0},
            {"target_fps": 31},
            {"camera_device_index": 33},
        )

        for index, invalid_values in enumerate(
            invalid_cases
        ):
            with self.subTest(
                invalid_values=invalid_values
            ):
                gate = Gate(
                    name=f"Invalid Gate {index}",
                    **invalid_values,
                )

                with self.assertRaises(ValidationError):
                    gate.full_clean()


class GateSerializerTests(TestCase):
    def test_serializer_exposes_camera_and_line_settings(self):
        gate = Gate.objects.create(
            name="Serializer Gate",
            gate_type=Gate.GateType.EXIT,
            camera_source=Gate.CameraSource.USB_CAMERA,
            camera_device_index=1,
            target_fps=10,
        )

        data = GateSerializer(gate).data

        expected_fields = {
            "id",
            "display_name",
            "name",
            "gate_type",
            "location",
            "camera_name",
            "camera_source",
            "camera_ip",
            "camera_device_index",
            "target_fps",
            "line_crossing_enabled",
            "line_start_x",
            "line_start_y",
            "line_end_x",
            "line_end_y",
            "crossing_direction",
            "is_active",
            "created_at",
        }

        self.assertEqual(set(data), expected_fields)
        self.assertEqual(data["gate_type"], "EXIT")
        self.assertEqual(data["camera_device_index"], 1)
        self.assertEqual(data["target_fps"], 10)

    def test_ip_and_rtsp_require_stream_address(self):
        stream_sources = (
            Gate.CameraSource.IP_CAMERA,
            Gate.CameraSource.RTSP,
        )

        for index, camera_source in enumerate(
            stream_sources
        ):
            with self.subTest(
                camera_source=camera_source
            ):
                serializer = GateSerializer(
                    data={
                        "name": f"Stream Gate {index}",
                        "camera_source": camera_source,
                    }
                )

                self.assertFalse(serializer.is_valid())
                self.assertIn(
                    "camera_ip",
                    serializer.errors,
                )

    def test_stream_address_is_trimmed(self):
        serializer = GateSerializer(
            data={
                "name": "Trimmed RTSP Gate",
                "camera_source": Gate.CameraSource.RTSP,
                "camera_ip": (
                    "  rtsp://192.168.1.20/live  "
                ),
            }
        )

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )
        self.assertEqual(
            serializer.validated_data["camera_ip"],
            "rtsp://192.168.1.20/live",
        )

    def test_serializer_rejects_identical_line_points(self):
        serializer = GateSerializer(
            data={
                "name": "Invalid Serializer Line",
                "line_crossing_enabled": True,
                "line_start_x": 0.4,
                "line_start_y": 0.6,
                "line_end_x": 0.4,
                "line_end_y": 0.6,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn(
            "line_end_x",
            serializer.errors,
        )
        self.assertIn(
            "line_end_y",
            serializer.errors,
        )

    def test_partial_update_uses_existing_coordinates(self):
        gate = Gate.objects.create(
            name="Partial Line Gate",
            line_start_x=0.2,
            line_start_y=0.3,
            line_end_x=0.8,
            line_end_y=0.7,
        )

        serializer = GateSerializer(
            gate,
            data={
                "line_end_x": 0.2,
                "line_end_y": 0.3,
            },
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn(
            "line_end_x",
            serializer.errors,
        )

    def test_legacy_stream_gate_can_update_other_fields(self):
        gate = Gate.objects.create(
            name="Legacy IP Gate",
            camera_source=Gate.CameraSource.IP_CAMERA,
            camera_ip=None,
        )

        serializer = GateSerializer(
            gate,
            data={"name": "Renamed Legacy IP Gate"},
            partial=True,
        )

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )

        updated_gate = serializer.save()

        self.assertEqual(
            updated_gate.name,
            "Renamed Legacy IP Gate",
        )


class GateApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.admin = User.objects.create_user(
            username="gate_admin",
            password="test-password",
            role=User.Role.ADMIN,
        )

        self.guard = User.objects.create_user(
            username="gate_guard",
            password="test-password",
            role=User.Role.SECURITY_GUARD,
        )

        self.viewer = User.objects.create_user(
            username="gate_viewer",
            password="test-password",
            role=User.Role.VIEWER,
        )

        self.gate = Gate.objects.create(
            name="Existing Test Gate",
            gate_type=Gate.GateType.ENTRY,
        )

        self.list_url = reverse("gate-list")
        self.detail_url = reverse(
            "gate-detail",
            args=[self.gate.pk],
        )

    def test_unauthenticated_user_cannot_list_gates(self):
        response = self.client.get(self.list_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_authenticated_viewer_can_list_gates(self):
        self.client.force_authenticate(self.viewer)

        response = self.client.get(self.list_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["name"],
            self.gate.name,
        )

    def test_viewer_cannot_create_gate(self):
        self.client.force_authenticate(self.viewer)

        response = self.client.post(
            self.list_url,
            {
                "name": "Forbidden Viewer Gate",
                "camera_source": (
                    Gate.CameraSource.WEBCAM
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertFalse(
            Gate.objects.filter(
                name="Forbidden Viewer Gate"
            ).exists()
        )

    def test_admin_can_create_gate_and_audit_is_saved(
        self
    ):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.list_url,
            {
                "name": "Admin Created Gate",
                "gate_type": Gate.GateType.EXIT,
                "camera_source": (
                    Gate.CameraSource.USB_CAMERA
                ),
                "camera_device_index": 1,
                "target_fps": 10,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        created_gate = Gate.objects.get(
            name="Admin Created Gate"
        )

        self.assertEqual(
            created_gate.gate_type,
            Gate.GateType.EXIT,
        )

        self.assertTrue(
            AuditLog.objects.filter(
                user=self.admin,
                action=AuditLog.Action.CREATE,
                model_name="Gate",
                object_id=str(created_gate.pk),
            ).exists()
        )

    def test_role_permission_allows_gate_creation(self):
        RolePermission.objects.create(
            role=self.guard.role,
            can_manage_settings=True,
        )

        self.client.force_authenticate(self.guard)

        response = self.client.post(
            self.list_url,
            {
                "name": "Guard Managed Gate",
                "camera_source": (
                    Gate.CameraSource.WEBCAM
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        self.assertTrue(
            AuditLog.objects.filter(
                user=self.guard,
                action=AuditLog.Action.CREATE,
                model_name="Gate",
            ).exists()
        )

    def test_viewer_cannot_update_gate(self):
        self.client.force_authenticate(self.viewer)

        response = self.client.patch(
            self.detail_url,
            {"target_fps": 12},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.gate.refresh_from_db()

        self.assertEqual(self.gate.target_fps, 10)

    def test_admin_can_update_gate_and_audit_is_saved(
        self
    ):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            self.detail_url,
            {
                "target_fps": 12,
                "line_start_y": 0.4,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.gate.refresh_from_db()

        self.assertEqual(self.gate.target_fps, 12)
        self.assertEqual(self.gate.line_start_y, 0.4)

        self.assertTrue(
            AuditLog.objects.filter(
                user=self.admin,
                action=AuditLog.Action.UPDATE,
                model_name="Gate",
                object_id=str(self.gate.pk),
            ).exists()
        )

    def test_admin_can_delete_gate_and_audit_is_saved(
        self
    ):
        self.client.force_authenticate(self.admin)

        gate_id = str(self.gate.pk)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

        self.assertFalse(
            Gate.objects.filter(
                pk=self.gate.pk
            ).exists()
        )

        self.assertTrue(
            AuditLog.objects.filter(
                user=self.admin,
                action=AuditLog.Action.DELETE,
                model_name="Gate",
                object_id=gate_id,
            ).exists()
        )