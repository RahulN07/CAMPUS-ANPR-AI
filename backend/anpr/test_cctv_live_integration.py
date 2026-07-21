"""Tests for the CCTV command's non-blocking live-publication hooks."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from django.test import SimpleTestCase, override_settings

from anpr.management.commands.run_cctv_anpr import Command
from anpr.vehicle_processor import VehicleProcessingResult
from anpr.vehicle_tracker import VehicleDetection


class RecordingPublisher:
    def __init__(self):
        self.frames = []
        self.statuses = []
        self.detections = []

    def submit_frame(self, jpeg, metadata=None):
        self.frames.append((jpeg, dict(metadata or {})))
        return True

    def submit_status(self, status):
        self.statuses.append(dict(status))
        return True

    def submit_detection(self, detection):
        self.detections.append(dict(detection))
        return True

    def stats(self):
        return SimpleNamespace(
            frames_dropped=2,
            frames_failed=1,
            statuses_failed=0,
            detections_failed=0,
        )


class NamedDepartment:
    def __str__(self):
        return "Computer Science Engineering"


def worker_stats(**overrides):
    values = {
        "queue_size": 3,
        "queue_capacity": 100,
        "in_flight": 2,
        "live_workers": 5,
        "failed": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def pipeline_stats(**overrides):
    values = {
        "frames_processed": 24,
        "vehicles_observed": 12,
        "line_crossings": 3,
        "tasks_submitted": 3,
        "tasks_rejected": 0,
        "records_saved": 2,
        "duplicate_results": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CctvLiveIntegrationTests(SimpleTestCase):
    def make_command(self):
        command = Command()
        command._gate = SimpleNamespace(
            id=7,
            name="Main Gate",
            gate_type="ENTRY",
            target_fps=10,
        )
        command._direction = "ENTRY"
        command._diagnostic_only = False
        command._live_publisher = RecordingPublisher()
        command._activity_lock = threading.Lock()
        command._latest_activity = None
        command._latest_activity_until = 0.0
        command._write_success = Mock()
        command._write_warning = Mock()
        command._write_error = Mock()
        return command

    @staticmethod
    def make_pipeline():
        return SimpleNamespace(
            worker_pool=SimpleNamespace(stats=lambda: worker_stats()),
            line_detector=SimpleNamespace(
                line_pixels=lambda width, height: (
                    (int(width * 0.1), int(height * 0.5)),
                    (int(width * 0.9), int(height * 0.5)),
                )
            ),
            stats=lambda: pipeline_stats(),
        )

    @staticmethod
    def make_frame_result():
        detections = (
            VehicleDetection(
                track_id=12,
                class_id=2,
                vehicle_type="car",
                confidence=0.91,
                bbox=(10, 20, 110, 90),
            ),
            VehicleDetection(
                track_id=None,
                class_id=3,
                vehicle_type="motorcycle",
                confidence=0.78,
                bbox=(140, 25, 210, 100),
            ),
        )
        return SimpleNamespace(
            frame_index=44,
            detections=detections,
            frame_processing_ms=18.25,
            tracker_inference_ms=14.5,
        )

    @override_settings(ANPR_LIVE_FRAME_JPEG_QUALITY=75)
    def test_annotated_frame_is_encoded_with_tracking_metadata(self):
        command = self.make_command()
        frame = np.zeros((120, 240, 3), dtype=np.uint8)

        submitted = command._submit_live_frame(
            frame=frame,
            pipeline=self.make_pipeline(),
            frame_result=self.make_frame_result(),
            fps=9.75,
        )

        self.assertTrue(submitted)
        self.assertEqual(len(command._live_publisher.frames), 1)
        jpeg, metadata = command._live_publisher.frames[0]
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertEqual(metadata["frame_index"], 44)
        self.assertEqual(metadata["vehicle_count"], 2)
        self.assertEqual(metadata["tracked_count"], 1)
        self.assertEqual(metadata["vehicle_queue_size"], 3)
        self.assertEqual(metadata["line"]["start"], [24, 60])
        self.assertEqual(metadata["detections"][0]["track_id"], 12)
        self.assertEqual(metadata["detections"][0]["bbox"], [10, 20, 110, 90])

        decoded = cv2.imdecode(
            np.frombuffer(jpeg, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        self.assertEqual(decoded.shape, frame.shape)

    def test_failed_jpeg_encoding_does_not_submit_frame(self):
        command = self.make_command()
        with patch(
            "anpr.management.commands.run_cctv_anpr.cv2.imencode",
            return_value=(False, None),
        ):
            submitted = command._submit_live_frame(
                frame=np.zeros((20, 20, 3), dtype=np.uint8),
                pipeline=self.make_pipeline(),
                frame_result=None,
                fps=0.0,
            )

        self.assertFalse(submitted)
        self.assertEqual(command._live_publisher.frames, [])
        command._write_warning.assert_called_once()

    def test_live_status_contains_camera_pipeline_and_queue_metrics(self):
        command = self.make_command()
        capture_stats = SimpleNamespace(
            running=True,
            opened=True,
            reconnects=1,
            queue=SimpleNamespace(size=4, maxsize=30, dropped=6),
        )
        capture_service = SimpleNamespace(stats=lambda: capture_stats)

        status = command._build_live_status(
            state="RUNNING",
            pipeline=self.make_pipeline(),
            capture_service=capture_service,
            frame_result=self.make_frame_result(),
            fps=9.8,
        )

        self.assertEqual(status["state"], "RUNNING")
        self.assertEqual(status["gate_name"], "Main Gate")
        self.assertEqual(status["direction"], "ENTRY")
        self.assertEqual(status["fps"], 9.8)
        self.assertEqual(status["vehicle_count"], 2)
        self.assertEqual(status["frame_queue_size"], 4)
        self.assertEqual(status["frame_queue_dropped"], 6)
        self.assertEqual(status["vehicle_queue_size"], 3)
        self.assertEqual(status["worker_in_flight"], 2)
        self.assertEqual(status["records_saved"], 2)
        self.assertEqual(status["duplicates_ignored"], 1)
        self.assertEqual(status["live_frames_dropped"], 2)
        self.assertEqual(status["live_publish_failures"], 1)

    def test_registered_vehicle_event_uses_trusted_vehicle_details(self):
        command = self.make_command()
        department = NamedDepartment()
        vehicle = SimpleNamespace(
            owner_name="Rahul Nayak",
            owner_type="STUDENT",
            department=department,
            vehicle_company="BMW",
            vehicle_model="3 Series",
            color="Black",
            vehicle_type="FOUR_WHEELER",
        )
        record = SimpleNamespace(
            pk=91,
            vehicle=vehicle,
            detected_vehicle_company="Wrong Company",
            detected_vehicle_model="Wrong Model",
            vehicle_color="White",
            detected_vehicle_type="car",
            timestamp=datetime(2026, 7, 21, 10, 30, tzinfo=timezone.utc),
            captured_image=SimpleNamespace(url="/media/full.jpg"),
            plate_image=SimpleNamespace(url="/media/plate.jpg"),
        )
        result = VehicleProcessingResult(
            track_id=12,
            saved=True,
            reason="SAVED",
            plate_text="KA02MM9091",
            authorization_status="AUTHORIZED",
            authorized=True,
            record_id=91,
            confidence=0.96,
            votes=3,
            candidates_attempted=3,
            processing_ms=220.0,
        )

        self.assertTrue(
            command._publish_detection_activity(
                track=SimpleNamespace(track_id=12),
                result=result,
                record=record,
            )
        )
        event = command._live_publisher.detections[0]
        self.assertEqual(event["record_id"], 91)
        self.assertEqual(event["plate"], "KA02MM9091")
        self.assertTrue(event["authorized"])
        self.assertEqual(event["owner"], "Rahul Nayak")
        self.assertEqual(event["department"], "Computer Science Engineering")
        self.assertEqual(event["company"], "BMW")
        self.assertEqual(event["model"], "3 Series")
        self.assertEqual(event["color"], "Black")
        self.assertEqual(event["gate"], "Main Gate")
        self.assertEqual(event["captured_image"], "/media/full.jpg")

    def test_unknown_vehicle_event_uses_detected_attributes(self):
        command = self.make_command()
        record = SimpleNamespace(
            pk=92,
            vehicle=None,
            detected_vehicle_company="Volvo",
            detected_vehicle_model="XC60",
            vehicle_color="Grey",
            detected_vehicle_type="car",
            timestamp=datetime.now(timezone.utc),
            captured_image=None,
            plate_image=None,
        )
        result = VehicleProcessingResult(
            track_id=6,
            saved=True,
            reason="SAVED",
            plate_text="KA02MN1826",
            authorization_status="UNKNOWN",
            authorized=False,
            record_id=92,
            confidence=0.93,
        )

        command._publish_detection_activity(
            track=SimpleNamespace(track_id=6),
            result=result,
            record=record,
        )
        event = command._live_publisher.detections[0]
        self.assertIsNone(event["owner"])
        self.assertEqual(event["company"], "Volvo")
        self.assertEqual(event["model"], "XC60")
        self.assertEqual(event["color"], "Grey")
        self.assertEqual(event["vehicle_type"], "car")

    def test_diagnostic_event_is_marked_and_does_not_require_record(self):
        command = self.make_command()
        command._diagnostic_only = True
        result = VehicleProcessingResult(
            track_id=5,
            saved=True,
            reason="SAVED",
            plate_text="KA25AB1234",
            authorization_status="UNKNOWN",
            authorized=False,
        )

        command._publish_detection_activity(
            track=SimpleNamespace(track_id=5),
            result=result,
            record=None,
        )
        event = command._live_publisher.detections[0]
        self.assertTrue(event["diagnostic_only"])
        self.assertIsNone(event["record_id"])
        self.assertIsNone(event["owner"])

    def test_saved_activity_publishes_event_and_creates_alert(self):
        command = self.make_command()
        record = SimpleNamespace(pk=44)
        command._get_activity_record = Mock(return_value=record)
        command._publish_detection_activity = Mock(return_value=True)
        command.create_notification = Mock()
        track = SimpleNamespace(track_id=14)
        result = VehicleProcessingResult(
            track_id=14,
            saved=True,
            reason="SAVED",
            plate_text="KA02MM9091",
            authorization_status="UNKNOWN",
            authorized=False,
            record_id=44,
        )

        command._handle_activity(track, result)

        command._get_activity_record.assert_called_once_with(44)
        command._publish_detection_activity.assert_called_once_with(
            track=track,
            result=result,
            record=record,
        )
        command.create_notification.assert_called_once_with(
            result,
            record=record,
        )

    def test_authorized_activity_does_not_create_unauthorized_alert(self):
        command = self.make_command()
        command._get_activity_record = Mock(return_value=SimpleNamespace(pk=45))
        command._publish_detection_activity = Mock(return_value=True)
        command.create_notification = Mock()
        result = VehicleProcessingResult(
            track_id=18,
            saved=True,
            reason="SAVED",
            plate_text="KA25AB1234",
            authorization_status="AUTHORIZED",
            authorized=True,
            record_id=45,
        )

        command._handle_activity(SimpleNamespace(track_id=18), result)

        command._publish_detection_activity.assert_called_once()
        command.create_notification.assert_not_called()

    def test_field_url_handles_empty_and_invalid_fields(self):
        command = self.make_command()
        self.assertIsNone(command._field_url(None))
        self.assertIsNone(command._field_url(SimpleNamespace()))
        self.assertEqual(
            command._field_url(SimpleNamespace(url="/media/evidence.jpg")),
            "/media/evidence.jpg",
        )