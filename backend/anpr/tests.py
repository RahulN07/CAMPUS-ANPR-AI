import queue
import threading
import time

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from anpr.detector import (
    clamp_bounding_box,
    clean_plate_text,
    detect_plate_bboxes,
    match_vehicle,
    run_full_pipeline,
)

from anpr.vehicle_cache_sync import (
    VehicleCacheRefreshService,
    VehicleCacheSyncConfig,
)

from django.db import connection
from django.test.utils import CaptureQueriesContext

from access_management.models import Department
from anpr.vehicle_cache import (
    VehicleCache,
    VehicleCacheError,
)

from anpr.plate_validation import (
    PlateFormat,
    clean_plate_text as clean_validated_plate_text,
    is_valid_indian_plate,
    validate_and_normalize_plate,
)
from anpr.vehicle_tracker import (
    VehicleTracker,
    VehicleTrackerConfig,
    VehicleTrackerError,
)

from anpr.camera_capture import CameraCaptureService

from anpr.frame_queue import (
    DropOldestQueue,
    FramePacket,
)

from anpr.line_crossing import (
    DIRECTION_ANY,
    DIRECTION_A_TO_B,
    DIRECTION_B_TO_A,
    LineCrossingConfig,
    LineCrossingDetector,
    LineCrossingEvent,
    NormalizedPoint,
)

from anpr.vehicle_worker_pool import (
    VehicleWorkerPool,
    WorkerPoolConfig,
    WorkerPoolState,
)


from anpr.track_buffer import (
    TrackBufferConfig,
    TrackCandidateBuffer,
)
from anpr.vehicle_tracker import VehicleDetection

from vehicles.models import Vehicle

from django.core.management import call_command
from django.core.management.base import CommandError

from access_management.models import Gate
from accounts.models import User
from anpr.management.commands.run_cctv_anpr import (
    Command as CCTVCommand,
)

from access_management.models import Gate
from accounts.models import User
from anpr.track_buffer import (
    FinalizedVehicleTrack,
    VehicleFrameCandidate,
)
from anpr.vehicle_cache import VehicleLookupResult
from anpr.vehicle_processor import (
    PlateObservation,
    PlateReservation,
    RecentPlateGuard,
    VehicleProcessor,
    VehicleProcessorConfig,
    VehicleRecordPayload,
)
from records.models import EntryExitRecord

from anpr.tracking_pipeline import (
    CameraTrackingPipeline,
    TrackingPipelineConfig,
)
from anpr.vehicle_tracker import (
    VehicleDetection,
    VehicleTrackingResult,
)
from anpr.vehicle_processor import (
    VehicleProcessingResult,
)
from anpr.vehicle_worker_pool import (
    WorkerPoolState,
    WorkerPoolStats,
)

class FakeBox:
    def __init__(
        self,
        confidence,
        coordinates,
        class_id=0,
    ):
        self.conf = np.array(
            [confidence],
            dtype=np.float32,
        )

        self.cls = np.array(
            [class_id],
            dtype=np.float32,
        )

        self.xyxy = np.array(
            [coordinates],
            dtype=np.float32,
        )


class FakeYoloModel:
    names = {
        0: "license_plate",
    }

    def __init__(self, boxes):
        self.boxes = boxes

    def predict(
        self,
        source,
        conf,
        verbose,
    ):
        return [
            SimpleNamespace(
                boxes=self.boxes,
                names=self.names,
            )
        ]


class DetectorUtilityTests(SimpleTestCase):
    def test_configured_model_exists(self):
        model_path = settings.ANPR_YOLO_MODEL_PATH

        self.assertTrue(
            model_path.is_absolute()
        )

        self.assertTrue(
            model_path.is_file(),
            f"Model file was not found: {model_path}",
        )

        self.assertEqual(
            model_path.suffix.lower(),
            ".pt",
        )

    def test_clean_plate_text(self):
        test_cases = {
            "ka 25-ab 1234": "KA25AB1234",
            " KA-01-MJ-0001 ": "KA01MJ0001",
            "mh.12.cd.4567": "MH12CD4567",
            "": "",
            "   ": "",
        }

        for raw_text, expected in test_cases.items():
            with self.subTest(raw_text=raw_text):
                self.assertEqual(
                    clean_plate_text(raw_text),
                    expected,
                )

    def test_clamp_bounding_box(self):
        image = np.zeros(
            (100, 200, 3),
            dtype=np.uint8,
        )

        result = clamp_bounding_box(
            [-20, -10, 250, 150],
            image,
        )

        self.assertEqual(
            result,
            [0, 0, 200, 100],
        )

    def test_invalid_bounding_box_returns_none(self):
        image = np.zeros(
            (100, 200, 3),
            dtype=np.uint8,
        )

        result = clamp_bounding_box(
            [100, 50, 50, 20],
            image,
        )

        self.assertIsNone(result)

    @patch("anpr.detector.get_yolo_model")
    def test_detects_all_plates_sorted_by_confidence(
        self,
        mocked_get_model,
    ):
        fake_model = FakeYoloModel(
            boxes=[
                FakeBox(
                    confidence=0.65,
                    coordinates=[10, 20, 90, 50],
                ),
                FakeBox(
                    confidence=0.94,
                    coordinates=[110, 30, 190, 65],
                ),
                FakeBox(
                    confidence=0.80,
                    coordinates=[40, 60, 130, 95],
                ),
            ]
        )

        mocked_get_model.return_value = fake_model

        image = np.zeros(
            (120, 220, 3),
            dtype=np.uint8,
        )

        detections = detect_plate_bboxes(
            image,
            confidence_threshold=0.4,
        )

        self.assertEqual(
            len(detections),
            3,
        )

        self.assertAlmostEqual(
            detections[0]["confidence"],
            0.94,
            places=2,
        )

        self.assertEqual(
            detections[0]["bounding_box"],
            [110, 30, 190, 65],
        )

        self.assertEqual(
            detections[1]["bounding_box"],
            [40, 60, 130, 95],
        )

        self.assertEqual(
            detections[2]["bounding_box"],
            [10, 20, 90, 50],
        )

    @patch("anpr.detector.get_yolo_model")
    def test_ignores_boxes_below_threshold(
        self,
        mocked_get_model,
    ):
        fake_model = FakeYoloModel(
            boxes=[
                FakeBox(
                    confidence=0.20,
                    coordinates=[10, 10, 80, 40],
                ),
                FakeBox(
                    confidence=0.85,
                    coordinates=[100, 20, 180, 60],
                ),
            ]
        )

        mocked_get_model.return_value = fake_model

        image = np.zeros(
            (100, 200, 3),
            dtype=np.uint8,
        )

        detections = detect_plate_bboxes(
            image,
            confidence_threshold=0.4,
        )

        self.assertEqual(
            len(detections),
            1,
        )

        self.assertEqual(
            detections[0]["bounding_box"],
            [100, 20, 180, 60],
        )

    def test_empty_image_data_fails_safely(self):
        result = run_full_pipeline(b"")

        self.assertFalse(result.success)

        self.assertEqual(
            result.error,
            "No image data was provided.",
        )

    @patch("anpr.detector.match_vehicle")
    @patch("anpr.detector.run_ocr")
    @patch("anpr.detector.detect_plate_bboxes")
    def test_pipeline_preserves_all_candidates(
        self,
        mocked_detect,
        mocked_ocr,
        mocked_match_vehicle,
    ):
        image = np.zeros(
            (120, 240, 3),
            dtype=np.uint8,
        )

        encode_success, encoded_image = cv2.imencode(
            ".jpg",
            image,
        )

        self.assertTrue(encode_success)

        candidates = [
            {
                "bounding_box": [20, 20, 120, 60],
                "confidence": 0.92,
                "class_id": 0,
                "class_name": "license_plate",
            },
            {
                "bounding_box": [130, 30, 220, 70],
                "confidence": 0.81,
                "class_id": 0,
                "class_name": "license_plate",
            },
        ]

        mocked_detect.return_value = candidates
        mocked_ocr.return_value = (
            "KA 25 AB 1234",
            0.88,
        )
        mocked_match_vehicle.return_value = (
            None,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )

        result = run_full_pipeline(
            encoded_image.tobytes()
        )

        self.assertTrue(result.success)

        self.assertEqual(
            result.cleaned_plate_text,
            "KA25AB1234",
        )

        self.assertEqual(
            result.bounding_box,
            [20, 20, 120, 60],
        )

        self.assertEqual(
            len(result.plate_candidates),
            2,
        )

        self.assertEqual(
            result.authorization_status,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )

        self.assertAlmostEqual(
            result.confidence_score,
            0.9,
            places=2,
        )


class VehicleMatchingTests(TestCase):
    def setUp(self):
        today = timezone.now().date()

        self.vehicle = Vehicle.objects.create(
            owner_name="Rahul",
            owner_email="rahul@example.com",
            owner_phone="9876543210",
            owner_type=Vehicle.OwnerType.STUDENT,
            department=None,
            vehicle_company="Honda",
            vehicle_model="Activa",
            vehicle_type=Vehicle.VehicleType.TWO_WHEELER,
            color="White",
            fuel_type=Vehicle.FuelType.PETROL,
            registration_number="KA25AB1234",
            registration_date=today,
            valid_from=today,
            valid_until=today + timedelta(days=30),
            authorization_status=(
                Vehicle.AuthorizationStatus.AUTHORIZED
            ),
        )

    def test_matches_registered_vehicle(self):
        vehicle, authorization_status = match_vehicle(
            "KA25AB1234"
        )

        self.assertIsNotNone(vehicle)

        self.assertEqual(
            vehicle.id,
            self.vehicle.id,
        )

        self.assertEqual(
            authorization_status,
            Vehicle.AuthorizationStatus.AUTHORIZED,
        )

    def test_unknown_plate_is_unauthorized(self):
        vehicle, authorization_status = match_vehicle(
            "KA01ZZ9999"
        )

        self.assertIsNone(vehicle)

        self.assertEqual(
            authorization_status,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )

    def test_expired_vehicle_is_reported_expired(self):
        self.vehicle.valid_until = (
            timezone.now().date()
            - timedelta(days=1)
        )

        self.vehicle.save()

        vehicle, authorization_status = match_vehicle(
            "KA25AB1234"
        )

        self.assertIsNotNone(vehicle)

        self.assertEqual(
            authorization_status,
            Vehicle.AuthorizationStatus.EXPIRED,
        )
class DuplicateFilterTests(TestCase):
    def setUp(self):
        import tempfile

        from django.test import override_settings
        from rest_framework.test import APIRequestFactory

        from access_management.models import Gate
        from accounts.models import User
        from anpr.views import DetectPlateView

        self.temporary_media = (
            tempfile.TemporaryDirectory()
        )

        self.media_override = override_settings(
            MEDIA_ROOT=self.temporary_media.name
        )

        self.media_override.enable()

        self.factory = APIRequestFactory()

        self.user = User.objects.create_user(
            username="security_test",
            password="StrongPassword123",
            role=User.Role.SECURITY_GUARD,
        )

        self.entry_gate = Gate.objects.create(
            name="Test Entry Gate",
            location="Main Entrance",
            is_active=True,
        )

        self.second_gate = Gate.objects.create(
            name="Second Entry Gate",
            location="Side Entrance",
            is_active=True,
        )

        self.view = DetectPlateView()

    def tearDown(self):
        self.media_override.disable()
        self.temporary_media.cleanup()

    def create_record(
        self,
        plate="KA25AB1234",
        direction="ENTRY",
        gate=None,
    ):
        from records.models import EntryExitRecord

        return EntryExitRecord.objects.create(
            detected_plate_text=plate,
            direction=direction,
            gate=gate or self.entry_gate,
            was_authorized=False,
            confidence_score=0.90,
            detection_source=(
                EntryExitRecord
                .DetectionSource
                .WEBCAM
            ),
            recorded_by=self.user,
        )

    def find_duplicate(
        self,
        plate="KA25AB1234",
        direction="ENTRY",
        gate=None,
    ):
        from records.models import EntryExitRecord

        return self.view._find_recent_duplicate(
            EntryExitRecord=EntryExitRecord,
            plate=plate,
            direction=direction,
            gate=gate or self.entry_gate,
        )

    def test_recent_same_plate_is_duplicate(self):
        record = self.create_record()

        duplicate = self.find_duplicate()

        self.assertIsNotNone(duplicate)

        self.assertEqual(
            duplicate.id,
            record.id,
        )

    def test_record_older_than_five_seconds_is_not_duplicate(
        self,
    ):
        from records.models import EntryExitRecord

        record = self.create_record()

        old_timestamp = (
            timezone.now()
            - timedelta(seconds=6)
        )

        EntryExitRecord.objects.filter(
            id=record.id
        ).update(
            timestamp=old_timestamp
        )

        duplicate = self.find_duplicate()

        self.assertIsNone(duplicate)

    def test_same_plate_at_different_gate_is_not_duplicate(
        self,
    ):
        self.create_record(
            gate=self.entry_gate
        )

        duplicate = self.find_duplicate(
            gate=self.second_gate
        )

        self.assertIsNone(duplicate)

    def test_same_plate_in_different_direction_is_not_duplicate(
        self,
    ):
        self.create_record(
            direction="ENTRY"
        )

        duplicate = self.find_duplicate(
            direction="EXIT"
        )

        self.assertIsNone(duplicate)

    @patch("anpr.views.run_full_pipeline")
    def test_duplicate_api_request_does_not_create_second_record(
        self,
        mocked_pipeline,
    ):
        from django.core.files.uploadedfile import (
            SimpleUploadedFile,
        )
        from rest_framework.test import (
            force_authenticate,
        )

        from anpr.detector import DetectionResult
        from anpr.views import DetectPlateView
        from records.models import EntryExitRecord

        mocked_pipeline.return_value = (
            DetectionResult(
                success=True,
                raw_plate_text="KA 25 AB 1234",
                cleaned_plate_text="KA25AB1234",
                confidence_score=0.90,
                bounding_box=[10, 10, 100, 50],
                plate_candidates=[
                    {
                        "bounding_box": [
                            10,
                            10,
                            100,
                            50,
                        ],
                        "confidence": 0.90,
                        "class_id": 0,
                        "class_name": (
                            "license_plate"
                        ),
                    }
                ],
                matched_vehicle=None,
                authorization_status=(
                    Vehicle
                    .AuthorizationStatus
                    .UNAUTHORIZED
                ),
                plate_crop_bytes=None,
            )
        )

        image = np.zeros(
            (100, 200, 3),
            dtype=np.uint8,
        )

        encoded_success, encoded_image = (
            cv2.imencode(
                ".jpg",
                image,
            )
        )

        self.assertTrue(encoded_success)

        image_bytes = encoded_image.tobytes()

        def send_request():
            uploaded_image = SimpleUploadedFile(
                "capture.jpg",
                image_bytes,
                content_type="image/jpeg",
            )

            request = self.factory.post(
                "/api/anpr/detect/",
                {
                    "image": uploaded_image,
                    "direction": "ENTRY",
                    "gate": self.entry_gate.id,
                    "source": "WEBCAM",
                },
                format="multipart",
            )

            force_authenticate(
                request,
                user=self.user,
            )

            return DetectPlateView.as_view()(
                request
            )

        first_response = send_request()
        second_response = send_request()

        self.assertEqual(
            first_response.status_code,
            201,
        )

        self.assertTrue(
            first_response.data[
                "entry_recorded"
            ]
        )

        self.assertFalse(
            first_response.data[
                "duplicate_ignored"
            ]
        )

        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.assertFalse(
            second_response.data[
                "entry_recorded"
            ]
        )

        self.assertTrue(
            second_response.data[
                "duplicate_ignored"
            ]
        )

        self.assertEqual(
            EntryExitRecord.objects.filter(
                detected_plate_text=(
                    "KA25AB1234"
                )
            ).count(),
            1,
        )

        self.assertEqual(
            first_response.data["record_id"],
            second_response.data["record_id"],
        )

class FrameQueueTests(SimpleTestCase):
    def test_maxsize_must_be_positive(self):
        invalid_sizes = (0, -1, -30)

        for maxsize in invalid_sizes:
            with self.subTest(maxsize=maxsize):
                with self.assertRaises(ValueError):
                    DropOldestQueue(maxsize=maxsize)

    def test_items_remain_fifo_before_queue_is_full(self):
        frame_queue = DropOldestQueue(maxsize=3)

        frame_queue.put_latest("frame-1")
        frame_queue.put_latest("frame-2")
        frame_queue.put_latest("frame-3")

        received = []

        for _index in range(3):
            received.append(frame_queue.get_nowait())
            frame_queue.task_done()

        self.assertEqual(
            received,
            [
                "frame-1",
                "frame-2",
                "frame-3",
            ],
        )

    def test_full_queue_discards_oldest_item(self):
        frame_queue = DropOldestQueue(maxsize=3)

        frame_queue.put_latest("frame-1")
        frame_queue.put_latest("frame-2")
        frame_queue.put_latest("frame-3")

        dropped = frame_queue.put_latest("frame-4")

        self.assertEqual(dropped, "frame-1")
        self.assertEqual(frame_queue.qsize(), 3)

        received = []

        for _index in range(3):
            received.append(frame_queue.get_nowait())
            frame_queue.task_done()

        self.assertEqual(
            received,
            [
                "frame-2",
                "frame-3",
                "frame-4",
            ],
        )

    def test_queue_statistics_track_accepted_and_dropped(self):
        frame_queue = DropOldestQueue(maxsize=2)

        frame_queue.put_latest(1)
        frame_queue.put_latest(2)
        frame_queue.put_latest(3)
        frame_queue.put_latest(4)

        stats = frame_queue.stats()

        self.assertEqual(stats.maxsize, 2)
        self.assertEqual(stats.size, 2)
        self.assertEqual(stats.accepted, 4)
        self.assertEqual(stats.dropped, 2)

    def test_clear_removes_pending_items(self):
        frame_queue = DropOldestQueue(maxsize=5)

        frame_queue.put_latest(1)
        frame_queue.put_latest(2)
        frame_queue.put_latest(3)

        removed_count = frame_queue.clear()

        self.assertEqual(removed_count, 3)
        self.assertTrue(frame_queue.empty())

        # clear() balances unfinished task counters.
        frame_queue.join()

        stats = frame_queue.stats()

        self.assertEqual(stats.accepted, 3)
        self.assertEqual(stats.dropped, 0)
        self.assertEqual(stats.size, 0)

    def test_empty_queue_get_honors_timeout(self):
        frame_queue = DropOldestQueue(maxsize=3)

        with self.assertRaises(queue.Empty):
            frame_queue.get(timeout=0.01)

    def test_multiple_producers_never_exceed_capacity(self):
        frame_queue = DropOldestQueue(maxsize=30)

        def produce(start_value):
            for offset in range(100):
                frame_queue.put_latest(
                    start_value + offset
                )

        threads = [
            threading.Thread(
                target=produce,
                args=(0,),
            ),
            threading.Thread(
                target=produce,
                args=(1000,),
            ),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        stats = frame_queue.stats()

        self.assertEqual(stats.maxsize, 30)
        self.assertEqual(stats.size, 30)
        self.assertEqual(stats.accepted, 200)
        self.assertEqual(stats.dropped, 170)

    def test_frame_packet_contains_capture_metadata(self):
        frame = np.zeros(
            (20, 30, 3),
            dtype=np.uint8,
        )

        packet = FramePacket(
            sequence=12,
            gate_id=4,
            frame=frame,
            source_name="camera-1",
        )

        self.assertEqual(packet.sequence, 12)
        self.assertEqual(packet.gate_id, 4)
        self.assertEqual(packet.source_name, "camera-1")
        self.assertIs(packet.frame, frame)
        self.assertTrue(
            timezone.is_aware(packet.captured_at)
        )
        self.assertGreater(
            packet.captured_monotonic,
            0,
        )

class FakeVideoCapture:
    def __init__(
        self,
        frames=None,
        *,
        opened=True,
        fps=30.0,
        read_delay=0.0,
        loop=False,
    ):
        self.frames = list(frames or [])
        self.opened = opened
        self.fps = fps
        self.read_delay = read_delay
        self.loop = loop

        self.frame_index = 0
        self.released = False
        self.buffer_size = None

    def isOpened(self):
        return self.opened and not self.released

    def read(self):
        if self.read_delay > 0:
            time.sleep(self.read_delay)

        if self.released or not self.opened:
            return False, None

        if self.loop and self.frames:
            frame = self.frames[
                self.frame_index % len(self.frames)
            ]
            self.frame_index += 1
            return True, frame.copy()

        if self.frame_index >= len(self.frames):
            return False, None

        frame = self.frames[self.frame_index]
        self.frame_index += 1

        return True, frame.copy()

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_FPS:
            return self.fps

        return 0.0

    def set(self, property_id, value):
        if property_id == cv2.CAP_PROP_BUFFERSIZE:
            self.buffer_size = value

        return True

    def release(self):
        self.released = True


class CaptureFactorySequence:
    def __init__(self, captures):
        self.captures = list(captures)
        self.call_count = 0

    def __call__(self, _source):
        self.call_count += 1

        if not self.captures:
            return FakeVideoCapture(opened=False)

        return self.captures.pop(0)


class CameraCaptureServiceTests(SimpleTestCase):
    def test_invalid_capture_configuration_is_rejected(self):
        invalid_configurations = (
            {"gate_id": 0},
            {"target_fps": 0},
            {"target_fps": 61},
            {"queue_size": 0},
            {"reconnect_delay": -0.1},
        )

        for configuration in invalid_configurations:
            with self.subTest(
                configuration=configuration
            ):
                options = {
                    "source": 0,
                    "gate_id": 1,
                }
                options.update(configuration)

                with self.assertRaises(ValueError):
                    CameraCaptureService(**options)

    def test_video_frames_are_captured_in_order(self):
        frames = [
            np.full(
                (12, 16, 3),
                fill_value=value,
                dtype=np.uint8,
            )
            for value in (10, 20, 30)
        ]

        fake_capture = FakeVideoCapture(
            frames=frames,
            fps=30.0,
        )

        service = CameraCaptureService(
            source="test-video.mp4",
            gate_id=7,
            target_fps=60,
            source_name="uploaded-video",
            replay_video_in_real_time=True,
            reconnect_delay=0.01,
            capture_factory=(
                lambda _source: fake_capture
            ),
        )

        service.start()

        self.assertTrue(
            service.wait_until_ready(timeout=1)
        )

        deadline = time.monotonic() + 2

        while (
            service.is_alive()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        self.assertFalse(service.is_alive())

        stats = service.stats()

        self.assertTrue(stats.ended)
        self.assertEqual(stats.frames_read, 3)
        self.assertEqual(stats.frames_enqueued, 3)
        self.assertEqual(stats.queue.size, 3)

        packets = []

        while not service.frame_queue.empty():
            packet = service.frame_queue.get_nowait()
            service.frame_queue.task_done()
            packets.append(packet)

        self.assertEqual(
            [packet.sequence for packet in packets],
            [1, 2, 3],
        )
        self.assertEqual(
            [packet.gate_id for packet in packets],
            [7, 7, 7],
        )
        self.assertEqual(
            [
                int(packet.frame[0, 0, 0])
                for packet in packets
            ],
            [10, 20, 30],
        )

    def test_initial_open_failure_is_retried(self):
        frame = np.zeros(
            (10, 10, 3),
            dtype=np.uint8,
        )

        factory = CaptureFactorySequence(
            [
                FakeVideoCapture(opened=False),
                FakeVideoCapture(
                    frames=[frame],
                    fps=30,
                    read_delay=0.002,
                    loop=True,
                ),
            ]
        )

        service = CameraCaptureService(
            source="rtsp://camera/live",
            gate_id=2,
            target_fps=10,
            reconnect_delay=0.01,
            capture_factory=factory,
        )

        service.start()

        self.assertTrue(
            service.wait_until_ready(timeout=1)
        )

        stats = service.stats()

        self.assertGreaterEqual(
            stats.open_failures,
            1,
        )
        self.assertGreaterEqual(factory.call_count, 2)

        self.assertTrue(
            service.stop(timeout=1)
        )

    def test_live_source_reconnects_after_read_failure(self):
        frame = np.zeros(
            (10, 10, 3),
            dtype=np.uint8,
        )

        first_capture = FakeVideoCapture(
            frames=[frame],
            fps=30,
        )

        second_capture = FakeVideoCapture(
            frames=[frame],
            fps=30,
            read_delay=0.002,
            loop=True,
        )

        factory = CaptureFactorySequence(
            [
                first_capture,
                second_capture,
            ]
        )

        service = CameraCaptureService(
            source="rtsp://camera/live",
            gate_id=3,
            target_fps=10,
            reconnect_delay=0.01,
            capture_factory=factory,
        )

        service.start()

        self.assertTrue(
            service.wait_until_ready(timeout=1)
        )

        deadline = time.monotonic() + 1

        while (
            service.stats().reconnects < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        stats = service.stats()

        self.assertGreaterEqual(
            stats.read_failures,
            1,
        )
        self.assertGreaterEqual(
            stats.reconnects,
            1,
        )

        self.assertTrue(
            service.stop(timeout=1)
        )

    def test_stop_can_clear_pending_frames(self):
        frame = np.zeros(
            (10, 10, 3),
            dtype=np.uint8,
        )

        fake_capture = FakeVideoCapture(
            frames=[frame],
            fps=30,
            read_delay=0.002,
            loop=True,
        )

        service = CameraCaptureService(
            source=0,
            gate_id=4,
            target_fps=60,
            capture_factory=(
                lambda _source: fake_capture
            ),
        )

        service.start()

        self.assertTrue(
            service.wait_until_ready(timeout=1)
        )

        deadline = time.monotonic() + 1

        while (
            service.frame_queue.empty()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        self.assertFalse(
            service.frame_queue.empty()
        )

        self.assertTrue(
            service.stop(
                timeout=1,
                clear_queue=True,
            )
        )

        self.assertTrue(service.frame_queue.empty())

        stats = service.stats()

        self.assertFalse(stats.running)
        self.assertFalse(stats.opened)

class CCTVCommandConfigurationTests(TestCase):
    def setUp(self):
        self.command = CCTVCommand()

        self.user = User.objects.create_user(
            username="cctv_operator",
            password="test-password",
            role=User.Role.SECURITY_GUARD,
        )

    def test_local_camera_source_uses_device_index(self):
        gate = Gate.objects.create(
            name="USB Camera Gate",
            camera_source=Gate.CameraSource.USB_CAMERA,
            camera_device_index=3,
        )

        source = self.command.resolve_source_argument(
            gate=gate
        )

        self.assertEqual(source, "3")

    def test_stream_gate_uses_configured_url(self):
        gate = Gate.objects.create(
            name="RTSP Camera Gate",
            camera_source=Gate.CameraSource.RTSP,
            camera_ip=(
                "rtsp://camera-user:secret@"
                "192.168.1.50/live"
            ),
        )

        source = self.command.resolve_source_argument(
            gate=gate
        )

        self.assertEqual(
            source,
            (
                "rtsp://camera-user:secret@"
                "192.168.1.50/live"
            ),
        )

    def test_stream_gate_requires_configured_url(self):
        gate = Gate.objects.create(
            name="Missing Stream Gate",
            camera_source=Gate.CameraSource.RTSP,
            camera_ip=None,
        )

        with self.assertRaises(CommandError):
            self.command.resolve_source_argument(
                gate=gate
            )

    def test_video_upload_requires_source_override(self):
        gate = Gate.objects.create(
            name="Uploaded Video Gate",
            camera_source=(
                Gate.CameraSource.VIDEO_UPLOAD
            ),
        )

        with self.assertRaises(CommandError):
            self.command.resolve_source_argument(
                gate=gate
            )

        source = self.command.resolve_source_argument(
            gate=gate,
            source_override="C:/videos/test.mp4",
        )

        self.assertEqual(
            source,
            "C:/videos/test.mp4",
        )

    def test_display_label_hides_stream_credentials(self):
        source = (
            "rtsp://camera-user:secret@"
            "192.168.1.50/live"
        )

        label = self.command.describe_source(source)

        self.assertEqual(label, "RTSP stream")
        self.assertNotIn("camera-user", label)
        self.assertNotIn("secret", label)
        self.assertNotIn("192.168.1.50", label)

    def test_conflicting_direction_is_rejected(self):
        gate = Gate.objects.create(
            name="Fixed Entry Gate",
            gate_type=Gate.GateType.ENTRY,
            camera_source=Gate.CameraSource.WEBCAM,
        )

        with self.assertRaisesMessage(
            CommandError,
            "--direction conflicts",
        ):
            call_command(
                "run_cctv_anpr",
                gate=gate.pk,
                recorded_by=self.user.pk,
                source="0",
                direction="EXIT",
            )

    def test_inactive_gate_cannot_start_capture(self):
        gate = Gate.objects.create(
            name="Inactive CCTV Gate",
            is_active=False,
            camera_source=Gate.CameraSource.WEBCAM,
        )

        with self.assertRaisesMessage(
            CommandError,
            "is inactive",
        ):
            call_command(
                "run_cctv_anpr",
                gate=gate.pk,
                recorded_by=self.user.pk,
                source="0",
            )

class IndianPlateValidationTests(SimpleTestCase):
    def test_validation_cleaner_removes_separators(self):
        self.assertEqual(
            clean_validated_plate_text(
                " ka 02-nh 7256 "
            ),
            "KA02NH7256",
        )

    def test_valid_standard_plates_are_accepted(self):
        valid_plates = (
            "KA02A1234",
            "KA02NH7256",
            "KA02ABC1234",
            "TG09AB1234",
            "TS09AB1234",
            "OR01AB1234",
        )

        for plate in valid_plates:
            with self.subTest(plate=plate):
                result = validate_and_normalize_plate(
                    plate
                )

                self.assertTrue(
                    result.is_valid,
                    result.reason,
                )
                self.assertEqual(
                    result.normalized_text,
                    plate,
                )
                self.assertEqual(
                    result.plate_format,
                    PlateFormat.STANDARD,
                )
                self.assertEqual(result.corrections, 0)

    def test_false_video_readings_are_rejected(self):
        false_readings = (
            "C102MN1829",
            "XA02NH7256",
            "KA02MM909F",
            "KA02MM909",
            "SUBSCRIBE",
        )

        for reading in false_readings:
            with self.subTest(reading=reading):
                result = validate_and_normalize_plate(
                    reading
                )

                self.assertFalse(result.is_valid)
                self.assertEqual(
                    result.normalized_text,
                    "",
                )
                self.assertFalse(
                    is_valid_indian_plate(reading)
                )

    def test_structurally_valid_video_readings_remain_candidates(
        self
    ):
        possible_readings = (
            "KA02NH7256",
            "KA02MH7256",
            "KA02MM9091",
        )

        for reading in possible_readings:
            with self.subTest(reading=reading):
                result = validate_and_normalize_plate(
                    reading
                )

                self.assertTrue(
                    result.is_valid,
                    result.reason,
                )

    def test_one_position_aware_correction_is_allowed(self):
        result = validate_and_normalize_plate(
            "KAO2NH7256"
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(
            result.normalized_text,
            "KA02NH7256",
        )
        self.assertEqual(result.corrections, 1)

    def test_excessive_corrections_are_rejected(self):
        result = validate_and_normalize_plate(
            "KAO2NHS2S6",
            max_corrections=1,
        )

        self.assertFalse(result.is_valid)

    def test_valid_bh_series_plate_is_accepted(self):
        result = validate_and_normalize_plate(
            "23 BH 1234 AB"
        )

        self.assertTrue(
            result.is_valid,
            result.reason,
        )
        self.assertEqual(
            result.normalized_text,
            "23BH1234AB",
        )
        self.assertEqual(
            result.plate_format,
            PlateFormat.BH_SERIES,
        )

    def test_invalid_bh_years_are_rejected(self):
        invalid_plates = (
            "20BH1234AB",
            "99BH1234AB",
        )

        for plate in invalid_plates:
            with self.subTest(plate=plate):
                result = validate_and_normalize_plate(
                    plate
                )

                self.assertFalse(result.is_valid)

    def test_zero_district_and_registration_are_rejected(
        self
    ):
        invalid_plates = (
            "KA00NH7256",
            "KA02NH0000",
            "23BH0000AB",
        )

        for plate in invalid_plates:
            with self.subTest(plate=plate):
                self.assertFalse(
                    validate_and_normalize_plate(
                        plate
                    ).is_valid
                )

    def test_edge_noise_is_disabled_by_default(self):
        raw_text = "XKA02NH7256"

        default_result = validate_and_normalize_plate(
            raw_text
        )

        edge_enabled_result = (
            validate_and_normalize_plate(
                raw_text,
                allow_edge_noise=True,
            )
        )

        self.assertFalse(default_result.is_valid)
        self.assertTrue(edge_enabled_result.is_valid)
        self.assertEqual(
            edge_enabled_result.normalized_text,
            "KA02NH7256",
        )
        self.assertEqual(
            edge_enabled_result.removed_edge_characters,
            1,
        )

    def test_negative_correction_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_and_normalize_plate(
                "KA02NH7256",
                max_corrections=-1,
            )
class DetectorPlateValidationIntegrationTests(
    SimpleTestCase
):
    def setUp(self):
        image = np.zeros(
            (100, 200, 3),
            dtype=np.uint8,
        )

        success, encoded_image = cv2.imencode(
            ".jpg",
            image,
        )

        self.assertTrue(success)
        self.image_bytes = encoded_image.tobytes()

        self.plate_candidates = [
            {
                "bounding_box": [20, 30, 180, 70],
                "confidence": 0.8,
                "class_id": 0,
                "class_name": "license_plate",
            }
        ]

    def run_pipeline_with_ocr(
        self,
        raw_text,
        ocr_confidence=0.9,
    ):
        with (
            patch(
                "anpr.detector.detect_plate_bboxes",
                return_value=self.plate_candidates,
            ),
            patch(
                "anpr.detector.run_ocr",
                return_value=(
                    raw_text,
                    ocr_confidence,
                ),
            ),
            patch(
                "anpr.detector.match_vehicle",
                return_value=(
                    None,
                    "UNAUTHORIZED",
                ),
            ) as match_vehicle_mock,
        ):
            result = run_full_pipeline(
                self.image_bytes
            )

        return result, match_vehicle_mock

    def test_false_video_readings_never_reach_database_lookup(
        self
    ):
        false_readings = (
            "C102MN1829",
            "XA02NH7256",
            "KA02MM909F",
            "KA02MM909",
            "SUBSCRIBE",
        )

        for reading in false_readings:
            with self.subTest(reading=reading):
                result, match_vehicle_mock = (
                    self.run_pipeline_with_ocr(
                        reading
                    )
                )

                self.assertFalse(result.success)
                self.assertIn(
                    "OCR text was rejected",
                    result.error,
                )
                self.assertEqual(
                    result.cleaned_plate_text,
                    reading,
                )
                match_vehicle_mock.assert_not_called()

    def test_position_correction_is_applied_before_lookup(
        self
    ):
        result, match_vehicle_mock = (
            self.run_pipeline_with_ocr(
                "KAO2NH7256",
                ocr_confidence=0.9,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.cleaned_plate_text,
            "KA02NH7256",
        )

        match_vehicle_mock.assert_called_once_with(
            "KA02NH7256"
        )

        # YOLO 0.80 + adjusted OCR 0.85, divided by 2.
        self.assertEqual(
            result.confidence_score,
            0.825,
        )

    def test_exact_valid_plate_keeps_original_confidence(
        self
    ):
        result, match_vehicle_mock = (
            self.run_pipeline_with_ocr(
                "KA02NH7256",
                ocr_confidence=0.9,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.cleaned_plate_text,
            "KA02NH7256",
        )
        self.assertEqual(
            result.confidence_score,
            0.85,
        )

        match_vehicle_mock.assert_called_once_with(
            "KA02NH7256"
        )

class FakeVehicleBoxes:
    def __init__(
        self,
        coordinates,
        confidences,
        class_ids,
        track_ids,
    ):
        self.xyxy = np.asarray(
            coordinates,
            dtype=np.float32,
        )
        self.conf = np.asarray(
            confidences,
            dtype=np.float32,
        )
        self.cls = np.asarray(
            class_ids,
            dtype=np.float32,
        )
        self.id = (
            None
            if track_ids is None
            else np.asarray(
                track_ids,
                dtype=np.float32,
            )
        )

    def __len__(self):
        return len(self.xyxy)


class FakeVehicleTrackingModel:
    names = {
        0: "person",
        1: "bicycle",
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
    }

    def __init__(
        self,
        boxes=None,
        error=None,
    ):
        self.boxes = boxes or FakeVehicleBoxes(
            [],
            [],
            [],
            [],
        )
        self.error = error
        self.calls = []

    def track(self, **kwargs):
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return [
            SimpleNamespace(
                boxes=self.boxes,
            )
        ]


class VehicleTrackerTests(SimpleTestCase):
    def test_tracker_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            VehicleTrackerConfig(confidence=0)

        with self.assertRaises(ValueError):
            VehicleTrackerConfig(confidence=1.1)

        with self.assertRaises(ValueError):
            VehicleTrackerConfig(iou=0)

        with self.assertRaises(ValueError):
            VehicleTrackerConfig(image_size=319)

        with self.assertRaises(ValueError):
            VehicleTrackerConfig(tracker="")

    def test_plate_only_model_is_rejected(self):
        plate_model = SimpleNamespace(
            names={
                0: "license_plate",
            }
        )

        with self.assertRaises(VehicleTrackerError):
            VehicleTracker(
                model=plate_model,
            )

    def test_supported_vehicle_class_ids_are_selected(self):
        model = FakeVehicleTrackingModel()
        tracker = VehicleTracker(model=model)

        self.assertEqual(
            tracker.vehicle_class_ids,
            (1, 2, 3, 5, 7),
        )

    def test_all_supported_vehicles_are_returned(self):
        boxes = FakeVehicleBoxes(
            coordinates=[
                [-10, 20, 700, 500],
                [100, 110, 300, 350],
                [320, 100, 600, 400],
            ],
            confidences=[
                0.91,
                0.82,
                0.76,
            ],
            class_ids=[
                2,
                3,
                7,
            ],
            track_ids=[
                12,
                13,
                14,
            ],
        )
        model = FakeVehicleTrackingModel(
            boxes=boxes,
        )
        tracker = VehicleTracker(model=model)
        frame = np.zeros(
            (480, 640, 3),
            dtype=np.uint8,
        )

        result = tracker.track(frame)

        self.assertEqual(
            result.vehicle_count,
            3,
        )
        self.assertEqual(
            result.tracked_count,
            3,
        )
        self.assertEqual(
            [
                detection.track_id
                for detection in result.detections
            ],
            [12, 13, 14],
        )
        self.assertEqual(
            [
                detection.vehicle_type
                for detection in result.detections
            ],
            [
                "car",
                "motorcycle",
                "truck",
            ],
        )
        self.assertEqual(
            result.detections[0].bbox,
            (0, 20, 640, 480),
        )
        self.assertEqual(
            result.detections[1].center,
            (200, 230),
        )
        self.assertGreaterEqual(
            result.inference_ms,
            0,
        )

        call = model.calls[0]

        self.assertTrue(call["persist"])
        self.assertEqual(
            call["tracker"],
            "bytetrack.yaml",
        )
        self.assertEqual(
            call["classes"],
            [1, 2, 3, 5, 7],
        )

        stats = tracker.stats()

        self.assertEqual(
            stats.frames_processed,
            1,
        )
        self.assertEqual(
            stats.vehicles_detected,
            3,
        )
        self.assertEqual(
            stats.failures,
            0,
        )

    def test_unsupported_classes_and_invalid_boxes_are_ignored(self):
        boxes = FakeVehicleBoxes(
            coordinates=[
                [10, 10, 50, 50],
                [100, 100, 100, 150],
                [20, 20, 80, 90],
            ],
            confidences=[
                0.9,
                0.8,
                0.7,
            ],
            class_ids=[
                0,
                2,
                5,
            ],
            track_ids=[
                1,
                2,
                3,
            ],
        )
        tracker = VehicleTracker(
            model=FakeVehicleTrackingModel(
                boxes=boxes,
            )
        )
        frame = np.zeros(
            (200, 300, 3),
            dtype=np.uint8,
        )

        result = tracker.track(frame)

        self.assertEqual(
            result.vehicle_count,
            1,
        )
        self.assertEqual(
            result.detections[0].vehicle_type,
            "bus",
        )
        self.assertEqual(
            result.detections[0].track_id,
            3,
        )

    def test_detection_without_track_id_is_preserved(self):
        boxes = FakeVehicleBoxes(
            coordinates=[
                [20, 30, 120, 150],
            ],
            confidences=[
                0.85,
            ],
            class_ids=[
                2,
            ],
            track_ids=None,
        )
        tracker = VehicleTracker(
            model=FakeVehicleTrackingModel(
                boxes=boxes,
            )
        )
        frame = np.zeros(
            (200, 300, 3),
            dtype=np.uint8,
        )

        result = tracker.track(frame)

        self.assertEqual(
            result.vehicle_count,
            1,
        )
        self.assertEqual(
            result.tracked_count,
            0,
        )
        self.assertIsNone(
            result.detections[0].track_id,
        )
        self.assertFalse(
            result.detections[0].is_tracked,
        )

    def test_invalid_frame_is_rejected_before_model_call(self):
        model = FakeVehicleTrackingModel()
        tracker = VehicleTracker(model=model)

        with self.assertRaises(TypeError):
            tracker.track("not-an-image")

        with self.assertRaises(ValueError):
            tracker.track(
                np.asarray(
                    [],
                    dtype=np.uint8,
                )
            )

        self.assertEqual(
            model.calls,
            [],
        )

    def test_model_failure_is_wrapped_and_counted(self):
        model = FakeVehicleTrackingModel(
            error=RuntimeError(
                "simulated inference failure"
            )
        )
        tracker = VehicleTracker(model=model)
        frame = np.zeros(
            (100, 100, 3),
            dtype=np.uint8,
        )

        with self.assertRaisesRegex(
            VehicleTrackerError,
            "simulated inference failure",
        ):
            tracker.track(frame)

        self.assertEqual(
            tracker.stats().failures,
            1,
        )

    def test_missing_vehicle_model_has_clear_error(self):
        tracker = VehicleTracker(
            model_source=(
                "definitely-missing-vehicle-model.pt"
            )
        )

        with self.assertRaisesRegex(
            VehicleTrackerError,
            "Vehicle model was not found",
        ):
            _ = tracker.vehicle_class_ids

    def test_trackers_keep_independent_model_state(self):
        first_model = FakeVehicleTrackingModel()
        second_model = FakeVehicleTrackingModel()

        first_tracker = VehicleTracker(
            model=first_model,
        )
        second_tracker = VehicleTracker(
            model=second_model,
        )
        frame = np.zeros(
            (100, 100, 3),
            dtype=np.uint8,
        )

        first_tracker.track(frame)
        second_tracker.track(frame)

        self.assertEqual(
            len(first_model.calls),
            1,
        )
        self.assertEqual(
            len(second_model.calls),
            1,
        )
        self.assertIsNot(
            first_tracker._model,
            second_tracker._model,
        )

class LineCrossingDetectorTests(SimpleTestCase):
    def make_config(self, **overrides):
        values = {
            "start": NormalizedPoint(0.2, 0.5),
            "end": NormalizedPoint(0.8, 0.5),
            "allowed_direction": DIRECTION_ANY,
            "enabled": True,
            "dead_zone": 0.008,
            "minimum_movement": 0.003,
            "segment_margin": 0.0,
            "max_idle_frames": 60,
        }
        values.update(overrides)
        return LineCrossingConfig(**values)

    def update(
        self,
        detector,
        track_id,
        x,
        y,
        frame_index,
    ):
        return detector.update(
            track_id=track_id,
            center=(x, y),
            frame_width=200,
            frame_height=200,
            frame_index=frame_index,
        )

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            NormalizedPoint(-0.1, 0.5)

        with self.assertRaises(ValueError):
            NormalizedPoint(0.5, 1.1)

        with self.assertRaises(ValueError):
            LineCrossingConfig(
                start=NormalizedPoint(0.5, 0.5),
                end=NormalizedPoint(0.5, 0.5),
            )

        with self.assertRaises(ValueError):
            self.make_config(
                allowed_direction="INVALID",
            )

        with self.assertRaises(ValueError):
            self.make_config(
                max_idle_frames=0,
            )

    def test_configuration_is_created_from_gate(self):
        gate = SimpleNamespace(
            line_start_x=0.1,
            line_start_y=0.4,
            line_end_x=0.9,
            line_end_y=0.6,
            crossing_direction=DIRECTION_B_TO_A,
            line_crossing_enabled=False,
        )

        config = LineCrossingConfig.from_gate(gate)

        self.assertEqual(
            config.start,
            NormalizedPoint(0.1, 0.4),
        )
        self.assertEqual(
            config.end,
            NormalizedPoint(0.9, 0.6),
        )
        self.assertEqual(
            config.allowed_direction,
            DIRECTION_B_TO_A,
        )
        self.assertFalse(config.enabled)

    def test_detects_a_to_b_crossing_once(self):
        detector = LineCrossingDetector(
            self.make_config()
        )

        first = self.update(
            detector,
            track_id=12,
            x=100,
            y=150,
            frame_index=0,
        )
        crossing = self.update(
            detector,
            track_id=12,
            x=100,
            y=50,
            frame_index=1,
        )
        duplicate = self.update(
            detector,
            track_id=12,
            x=100,
            y=150,
            frame_index=2,
        )

        self.assertIsNone(first)
        self.assertIsNotNone(crossing)
        self.assertEqual(
            crossing.track_id,
            12,
        )
        self.assertEqual(
            crossing.physical_direction,
            DIRECTION_A_TO_B,
        )
        self.assertAlmostEqual(
            crossing.intersection_point.y,
            0.5,
        )
        self.assertIsNone(duplicate)
        self.assertEqual(
            detector.stats().crossings,
            1,
        )

    def test_detects_b_to_a_crossing(self):
        detector = LineCrossingDetector(
            self.make_config()
        )

        self.update(
            detector,
            track_id=7,
            x=100,
            y=50,
            frame_index=0,
        )
        crossing = self.update(
            detector,
            track_id=7,
            x=100,
            y=150,
            frame_index=1,
        )

        self.assertIsNotNone(crossing)
        self.assertEqual(
            crossing.physical_direction,
            DIRECTION_B_TO_A,
        )

    def test_wrong_direction_is_rejected(self):
        detector = LineCrossingDetector(
            self.make_config(
                allowed_direction=DIRECTION_A_TO_B,
            )
        )

        self.update(
            detector,
            track_id=5,
            x=100,
            y=50,
            frame_index=0,
        )
        rejected = self.update(
            detector,
            track_id=5,
            x=100,
            y=150,
            frame_index=1,
        )
        accepted = self.update(
            detector,
            track_id=5,
            x=100,
            y=50,
            frame_index=2,
        )

        self.assertIsNone(rejected)
        self.assertIsNotNone(accepted)
        self.assertEqual(
            accepted.physical_direction,
            DIRECTION_A_TO_B,
        )

        stats = detector.stats()

        self.assertEqual(
            stats.rejected_direction,
            1,
        )
        self.assertEqual(
            stats.crossings,
            1,
        )

    def test_crossing_outside_finite_line_is_ignored(self):
        detector = LineCrossingDetector(
            self.make_config(
                segment_margin=0.0,
            )
        )

        self.update(
            detector,
            track_id=9,
            x=195,
            y=150,
            frame_index=0,
        )
        result = self.update(
            detector,
            track_id=9,
            x=195,
            y=50,
            frame_index=1,
        )

        self.assertIsNone(result)
        self.assertEqual(
            detector.stats().crossings,
            0,
        )

    def test_dead_zone_prevents_line_jitter(self):
        detector = LineCrossingDetector(
            self.make_config(
                dead_zone=0.02,
            )
        )

        results = [
            self.update(
                detector,
                track_id=20,
                x=100,
                y=99,
                frame_index=0,
            ),
            self.update(
                detector,
                track_id=20,
                x=100,
                y=101,
                frame_index=1,
            ),
            self.update(
                detector,
                track_id=20,
                x=100,
                y=98,
                frame_index=2,
            ),
            self.update(
                detector,
                track_id=20,
                x=100,
                y=102,
                frame_index=3,
            ),
        ]

        self.assertTrue(
            all(result is None for result in results)
        )
        self.assertEqual(
            detector.stats().crossings,
            0,
        )

    def test_disabled_line_never_emits_event(self):
        detector = LineCrossingDetector(
            self.make_config(
                enabled=False,
            )
        )

        self.update(
            detector,
            track_id=30,
            x=100,
            y=150,
            frame_index=0,
        )
        result = self.update(
            detector,
            track_id=30,
            x=100,
            y=50,
            frame_index=1,
        )

        self.assertIsNone(result)
        self.assertEqual(
            detector.stats().crossings,
            0,
        )

    def test_stale_tracks_are_removed_and_id_can_be_reused(self):
        detector = LineCrossingDetector(
            self.make_config(
                max_idle_frames=2,
            )
        )

        self.update(
            detector,
            track_id=1,
            x=100,
            y=150,
            frame_index=0,
        )
        self.update(
            detector,
            track_id=2,
            x=100,
            y=150,
            frame_index=3,
        )

        reused_result = self.update(
            detector,
            track_id=1,
            x=100,
            y=50,
            frame_index=4,
        )

        self.assertIsNone(reused_result)
        self.assertEqual(
            detector.stats().stale_tracks_removed,
            1,
        )

    def test_frame_index_cannot_move_backwards(self):
        detector = LineCrossingDetector(
            self.make_config()
        )

        self.update(
            detector,
            track_id=40,
            x=100,
            y=150,
            frame_index=5,
        )

        with self.assertRaises(ValueError):
            self.update(
                detector,
                track_id=40,
                x=100,
                y=140,
                frame_index=4,
            )

    def test_line_pixel_coordinates_are_available_for_preview(self):
        detector = LineCrossingDetector(
            self.make_config()
        )

        start, end = detector.line_pixels(
            frame_width=201,
            frame_height=101,
        )

        self.assertEqual(
            start,
            (40, 50),
        )
        self.assertEqual(
            end,
            (160, 50),
        )

    def test_remove_and_reset_clear_track_state(self):
        detector = LineCrossingDetector(
            self.make_config()
        )

        self.update(
            detector,
            track_id=50,
            x=100,
            y=150,
            frame_index=0,
        )

        self.assertTrue(
            detector.remove_track(50)
        )
        self.assertFalse(
            detector.remove_track(50)
        )

        self.update(
            detector,
            track_id=51,
            x=100,
            y=150,
            frame_index=1,
        )
        detector.reset()

        stats = detector.stats()

        self.assertEqual(stats.updates, 0)
        self.assertEqual(stats.crossings, 0)
        self.assertEqual(stats.active_tracks, 0)


class TrackCandidateBufferTests(SimpleTestCase):
    def make_frame(self, value=0):
        return np.full(
            (200, 300, 3),
            value,
            dtype=np.uint8,
        )

    def make_sharp_frame(self):
        rows, columns = np.indices((200, 300))
        pattern = (
            ((rows + columns) % 2) * 255
        ).astype(np.uint8)

        return np.repeat(
            pattern[:, :, np.newaxis],
            3,
            axis=2,
        )

    def make_detection(
        self,
        *,
        track_id=12,
        confidence=0.9,
        vehicle_type="car",
        bbox=(50, 50, 250, 150),
    ):
        return VehicleDetection(
            track_id=track_id,
            class_id=2,
            vehicle_type=vehicle_type,
            confidence=confidence,
            bbox=bbox,
        )

    def make_crossing(
        self,
        track_id=12,
        frame_index=10,
    ):
        return LineCrossingEvent(
            track_id=track_id,
            physical_direction=DIRECTION_A_TO_B,
            previous_point=NormalizedPoint(
                0.5,
                0.7,
            ),
            current_point=NormalizedPoint(
                0.5,
                0.3,
            ),
            intersection_point=NormalizedPoint(
                0.5,
                0.5,
            ),
            frame_index=frame_index,
        )

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            TrackBufferConfig(
                candidates_per_track=0,
            )

        with self.assertRaises(ValueError):
            TrackBufferConfig(
                candidates_per_track=11,
            )

        with self.assertRaises(ValueError):
            TrackBufferConfig(
                max_active_tracks=0,
            )

        with self.assertRaises(ValueError):
            TrackBufferConfig(
                max_idle_frames=0,
            )

        with self.assertRaises(ValueError):
            TrackBufferConfig(
                minimum_crop_width=1,
            )

    def test_untracked_detection_is_ignored(self):
        buffer = TrackCandidateBuffer()
        detection = self.make_detection(
            track_id=None,
        )

        retained = buffer.observe(
            frame=self.make_frame(),
            detection=detection,
            frame_index=0,
        )

        self.assertFalse(retained)

        stats = buffer.stats()

        self.assertEqual(stats.observations, 1)
        self.assertEqual(stats.crops_rejected, 1)
        self.assertEqual(stats.active_tracks, 0)

    def test_small_and_invalid_crops_are_rejected(self):
        buffer = TrackCandidateBuffer()

        small_result = buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(
                track_id=1,
                bbox=(10, 10, 20, 20),
            ),
            frame_index=0,
        )
        invalid_result = buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(
                track_id=2,
                bbox=(100, 50, 100, 150),
            ),
            frame_index=1,
        )

        self.assertFalse(small_result)
        self.assertFalse(invalid_result)
        self.assertEqual(
            buffer.stats().crops_rejected,
            2,
        )

    def test_only_top_ranked_candidates_are_retained(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                candidates_per_track=2,
            )
        )
        frame = self.make_frame()

        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.2,
            ),
            frame_index=0,
        )
        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.9,
            ),
            frame_index=1,
        )
        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.6,
            ),
            frame_index=2,
        )

        task = buffer.finalize(
            self.make_crossing(
                frame_index=3,
            )
        )

        self.assertIsNotNone(task)
        self.assertEqual(
            len(task.candidates),
            2,
        )
        self.assertEqual(
            [
                candidate.frame_index
                for candidate in task.candidates
            ],
            [1, 2],
        )

    def test_sharp_crop_outranks_blurry_crop(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                candidates_per_track=2,
            )
        )

        buffer.observe(
            frame=self.make_frame(100),
            detection=self.make_detection(),
            frame_index=0,
        )
        buffer.observe(
            frame=self.make_sharp_frame(),
            detection=self.make_detection(),
            frame_index=1,
        )

        task = buffer.finalize(
            self.make_crossing(
                frame_index=2,
            )
        )

        self.assertEqual(
            task.best_candidate.frame_index,
            1,
        )
        self.assertGreater(
            task.candidates[0].sharpness,
            task.candidates[1].sharpness,
        )

    def test_crop_is_copied_from_camera_frame(self):
        buffer = TrackCandidateBuffer()
        frame = self.make_frame(10)

        buffer.observe(
            frame=frame,
            detection=self.make_detection(),
            frame_index=0,
            captured_at=123.5,
        )

        frame.fill(255)

        task = buffer.finalize(
            self.make_crossing()
        )
        candidate = task.best_candidate

        self.assertTrue(
            np.all(candidate.crop == 10)
        )
        self.assertEqual(
            candidate.captured_at,
            123.5,
        )

    def test_bbox_is_clamped_to_frame(self):
        buffer = TrackCandidateBuffer()

        retained = buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(
                bbox=(-20, -30, 350, 250),
            ),
            frame_index=0,
        )

        task = buffer.finalize(
            self.make_crossing()
        )

        self.assertTrue(retained)
        self.assertEqual(
            task.best_candidate.source_bbox,
            (0, 0, 300, 200),
        )
        self.assertEqual(
            task.best_candidate.crop.shape,
            (200, 300, 3),
        )

    def test_vehicle_type_uses_weighted_track_votes(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                candidates_per_track=1,
            )
        )
        frame = self.make_frame()

        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.5,
                vehicle_type="car",
            ),
            frame_index=0,
        )
        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.9,
                vehicle_type="motorcycle",
            ),
            frame_index=1,
        )
        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                confidence=0.5,
                vehicle_type="car",
            ),
            frame_index=2,
        )

        task = buffer.finalize(
            self.make_crossing(
                frame_index=3,
            )
        )

        self.assertEqual(
            task.vehicle_type,
            "car",
        )

    def test_track_can_only_be_finalized_once(self):
        buffer = TrackCandidateBuffer()

        buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(),
            frame_index=0,
        )

        first = buffer.finalize(
            self.make_crossing(
                frame_index=1,
            )
        )
        second = buffer.finalize(
            self.make_crossing(
                frame_index=2,
            )
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            buffer.stats().tasks_finalized,
            1,
        )
        self.assertEqual(
            buffer.stats().duplicates_ignored,
            1,
        )

    def test_finalized_track_is_ignored_during_hold_period(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                duplicate_hold_frames=2,
            )
        )
        frame = self.make_frame()

        buffer.observe(
            frame=frame,
            detection=self.make_detection(),
            frame_index=0,
        )
        buffer.finalize(
            self.make_crossing(
                frame_index=1,
            )
        )

        blocked = buffer.observe(
            frame=frame,
            detection=self.make_detection(),
            frame_index=2,
        )
        accepted_after_expiry = buffer.observe(
            frame=frame,
            detection=self.make_detection(),
            frame_index=4,
        )

        self.assertFalse(blocked)
        self.assertTrue(accepted_after_expiry)

    def test_stale_tracks_are_pruned(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                max_idle_frames=2,
            )
        )

        buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(
                track_id=1,
            ),
            frame_index=0,
        )
        buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(
                track_id=2,
            ),
            frame_index=3,
        )

        stats = buffer.stats()

        self.assertEqual(
            stats.stale_tracks_removed,
            1,
        )
        self.assertEqual(
            stats.active_tracks,
            1,
        )

    def test_oldest_track_is_evicted_at_capacity(self):
        buffer = TrackCandidateBuffer(
            TrackBufferConfig(
                max_active_tracks=1,
            )
        )
        frame = self.make_frame()

        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                track_id=1,
            ),
            frame_index=0,
        )
        buffer.observe(
            frame=frame,
            detection=self.make_detection(
                track_id=2,
            ),
            frame_index=1,
        )

        missing = buffer.finalize(
            self.make_crossing(
                track_id=1,
                frame_index=2,
            )
        )
        retained = buffer.finalize(
            self.make_crossing(
                track_id=2,
                frame_index=2,
            )
        )

        self.assertIsNone(missing)
        self.assertIsNotNone(retained)
        self.assertEqual(
            buffer.stats().capacity_evictions,
            1,
        )

    def test_frame_index_cannot_move_backwards(self):
        buffer = TrackCandidateBuffer()
        frame = self.make_frame()

        buffer.observe(
            frame=frame,
            detection=self.make_detection(),
            frame_index=5,
        )

        with self.assertRaises(ValueError):
            buffer.observe(
                frame=frame,
                detection=self.make_detection(),
                frame_index=4,
            )

    def test_remove_and_reset_clear_state_and_statistics(self):
        buffer = TrackCandidateBuffer()

        buffer.observe(
            frame=self.make_frame(),
            detection=self.make_detection(),
            frame_index=0,
        )

        self.assertTrue(
            buffer.remove_track(12)
        )
        self.assertFalse(
            buffer.remove_track(12)
        )

        buffer.reset()
        stats = buffer.stats()

        self.assertEqual(stats.observations, 0)
        self.assertEqual(stats.active_tracks, 0)
        self.assertEqual(
            stats.retained_candidates,
            0,
        )

class VehicleWorkerPoolTests(SimpleTestCase):
    def make_config(self, **overrides):
        values = {
            "worker_count": 2,
            "queue_size": 10,
            "thread_name_prefix": "test-anpr-worker",
            "manage_django_connections": False,
        }
        values.update(overrides)
        return WorkerPoolConfig(**values)

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            WorkerPoolConfig(worker_count=0)

        with self.assertRaises(ValueError):
            WorkerPoolConfig(worker_count=33)

        with self.assertRaises(ValueError):
            WorkerPoolConfig(queue_size=0)

        with self.assertRaises(ValueError):
            WorkerPoolConfig(
                thread_name_prefix=" ",
            )

    def test_exactly_one_processor_source_is_required(self):
        with self.assertRaises(ValueError):
            VehicleWorkerPool()

        with self.assertRaises(ValueError):
            VehicleWorkerPool(
                processor=lambda task: task,
                processor_factory=lambda: (
                    lambda task: task
                ),
            )

    def test_submit_before_start_is_rejected(self):
        pool = VehicleWorkerPool(
            processor=lambda task: task,
            config=self.make_config(),
        )

        self.assertFalse(pool.submit("vehicle"))
        self.assertEqual(
            pool.stats().rejected_not_running,
            1,
        )
        self.assertTrue(pool.stop())
        self.assertEqual(
            pool.state,
            WorkerPoolState.STOPPED,
        )

    def test_all_tasks_are_processed_with_callbacks(self):
        callback_results = []
        callback_lock = threading.Lock()

        def processor(value):
            return value * 2

        def on_success(task, result):
            with callback_lock:
                callback_results.append(
                    (task, result)
                )

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(
                worker_count=3,
                queue_size=20,
            ),
            on_success=on_success,
        )

        pool.start()

        try:
            for value in range(10):
                self.assertTrue(
                    pool.submit(value)
                )

            self.assertTrue(
                pool.wait_until_idle(2)
            )

            stats = pool.stats()

            self.assertEqual(stats.submitted, 10)
            self.assertEqual(stats.completed, 10)
            self.assertEqual(stats.failed, 0)
            self.assertEqual(stats.in_flight, 0)
            self.assertEqual(stats.queue_size, 0)
            self.assertCountEqual(
                callback_results,
                [
                    (value, value * 2)
                    for value in range(10)
                ],
            )
        finally:
            pool.stop(timeout=2)

    def test_processor_failure_is_recorded_and_worker_continues(self):
        errors = []

        def processor(value):
            if value == "bad":
                raise RuntimeError(
                    "simulated OCR failure"
                )
            return value

        def on_error(task, error):
            errors.append(
                (task, str(error))
            )

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(),
            on_error=on_error,
        )
        pool.start()

        try:
            self.assertTrue(pool.submit("bad"))
            self.assertTrue(pool.submit("good"))
            self.assertTrue(
                pool.wait_until_idle(2)
            )

            stats = pool.stats()

            self.assertEqual(stats.failed, 1)
            self.assertEqual(stats.completed, 1)
            self.assertIn(
                "simulated OCR failure",
                stats.last_error,
            )
            self.assertEqual(
                errors,
                [
                    (
                        "bad",
                        "simulated OCR failure",
                    )
                ],
            )
        finally:
            pool.stop(timeout=2)

    def test_factory_creates_processor_per_worker(self):
        factory_calls = []
        factory_lock = threading.Lock()

        def processor_factory():
            with factory_lock:
                factory_calls.append(
                    threading.current_thread().name
                )

            return lambda task: task

        pool = VehicleWorkerPool(
            processor_factory=processor_factory,
            config=self.make_config(
                worker_count=3,
            ),
        )
        pool.start()

        try:
            deadline = time.monotonic() + 2

            while (
                pool.stats().live_workers < 3
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)

            self.assertEqual(
                pool.stats().live_workers,
                3,
            )
            self.assertEqual(
                len(factory_calls),
                3,
            )
            self.assertEqual(
                len(set(factory_calls)),
                3,
            )
        finally:
            pool.stop(timeout=2)

    def test_full_queue_rejects_without_blocking(self):
        processing_started = threading.Event()
        release_processor = threading.Event()

        def processor(value):
            processing_started.set()
            release_processor.wait(2)
            return value

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(
                worker_count=1,
                queue_size=1,
            ),
        )
        pool.start()

        try:
            self.assertTrue(pool.submit(1))
            self.assertTrue(
                processing_started.wait(1)
            )
            self.assertTrue(pool.submit(2))

            started = time.perf_counter()
            accepted = pool.submit(3)
            elapsed = time.perf_counter() - started

            self.assertFalse(accepted)
            self.assertLess(elapsed, 0.2)
            self.assertEqual(
                pool.stats().rejected_full,
                1,
            )
        finally:
            release_processor.set()
            pool.stop(timeout=2)

    def test_wait_until_idle_can_timeout(self):
        processing_started = threading.Event()
        release_processor = threading.Event()

        def processor(value):
            processing_started.set()
            release_processor.wait(2)
            return value

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(
                worker_count=1,
            ),
        )
        pool.start()

        try:
            pool.submit("vehicle")
            self.assertTrue(
                processing_started.wait(1)
            )
            self.assertFalse(
                pool.wait_until_idle(0.01)
            )

            release_processor.set()

            self.assertTrue(
                pool.wait_until_idle(2)
            )
        finally:
            release_processor.set()
            pool.stop(timeout=2)

    def test_stop_with_drain_completes_accepted_tasks(self):
        processed = []
        processed_lock = threading.Lock()

        def processor(value):
            with processed_lock:
                processed.append(value)
            return value

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(
                worker_count=3,
                queue_size=20,
            ),
        )
        pool.start()

        for value in range(10):
            self.assertTrue(
                pool.submit(value)
            )

        self.assertTrue(
            pool.stop(
                drain=True,
                timeout=2,
            )
        )
        self.assertCountEqual(
            processed,
            list(range(10)),
        )
        self.assertEqual(
            pool.stats().completed,
            10,
        )
        self.assertEqual(
            pool.state,
            WorkerPoolState.STOPPED,
        )

    def test_stop_without_drain_discards_queued_tasks(self):
        processing_started = threading.Event()
        release_processor = threading.Event()

        def processor(value):
            processing_started.set()
            release_processor.wait(2)
            return value

        pool = VehicleWorkerPool(
            processor=processor,
            config=self.make_config(
                worker_count=1,
                queue_size=2,
            ),
        )
        pool.start()

        self.assertTrue(pool.submit(1))
        self.assertTrue(
            processing_started.wait(1)
        )
        self.assertTrue(pool.submit(2))
        self.assertTrue(pool.submit(3))

        stopped_immediately = pool.stop(
            drain=False,
            timeout=0.01,
        )

        self.assertFalse(stopped_immediately)
        self.assertEqual(
            pool.state,
            WorkerPoolState.STOPPING,
        )
        self.assertEqual(
            pool.stats().discarded_on_stop,
            2,
        )
        self.assertFalse(pool.submit(4))

        release_processor.set()

        self.assertTrue(
            pool.stop(
                drain=True,
                timeout=2,
            )
        )
        self.assertEqual(
            pool.stats().completed,
            1,
        )
        self.assertEqual(
            pool.state,
            WorkerPoolState.STOPPED,
        )

    def test_pool_lifecycle_is_safe(self):
        pool = VehicleWorkerPool(
            processor=lambda task: task,
            config=self.make_config(),
        )

        self.assertTrue(pool.start())
        self.assertFalse(pool.start())
        self.assertTrue(
            pool.stop(timeout=2)
        )
        self.assertTrue(
            pool.stop(timeout=2)
        )

        with self.assertRaises(RuntimeError):
            pool.start()


class VehicleCacheTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.department = Department.objects.create(
            name=Department.Code.CSE,
        )
        self.vehicle = Vehicle.objects.create(
            owner_name="Rahul Nayak",
            owner_email="rahul@example.com",
            owner_phone="9876543210",
            owner_type=Vehicle.OwnerType.STUDENT,
            department=self.department,
            vehicle_company="Honda",
            vehicle_model="City",
            vehicle_type=Vehicle.VehicleType.FOUR_WHEELER,
            color="White",
            fuel_type=Vehicle.FuelType.PETROL,
            registration_number="KA25AB1234",
            registration_date=(
                self.today - timedelta(days=500)
            ),
            valid_from=(
                self.today - timedelta(days=30)
            ),
            valid_until=(
                self.today + timedelta(days=365)
            ),
            authorization_status=(
                Vehicle.AuthorizationStatus.AUTHORIZED
            ),
        )
        self.cache = VehicleCache()

    def test_refresh_loads_all_registered_vehicles(self):
        count = self.cache.refresh()
        stats = self.cache.stats()

        self.assertEqual(count, 1)
        self.assertTrue(stats.loaded)
        self.assertEqual(stats.vehicle_count, 1)
        self.assertEqual(stats.refreshes, 1)
        self.assertEqual(stats.version, 1)
        self.assertIsNotNone(stats.loaded_at)

    def test_refresh_uses_one_database_query(self):
        with CaptureQueriesContext(
            connection
        ) as queries:
            self.cache.refresh()

        self.assertEqual(
            len(queries),
            1,
        )

    def test_lookup_is_normalized_and_uses_zero_queries(self):
        self.cache.refresh()

        with CaptureQueriesContext(
            connection
        ) as queries:
            cached = self.cache.lookup(
                "ka 25-ab 1234"
            )

        self.assertEqual(
            len(queries),
            0,
        )
        self.assertIsNotNone(cached)
        self.assertEqual(
            cached.registration_number,
            "KA25AB1234",
        )

    def test_snapshot_contains_frontend_vehicle_information(self):
        self.cache.refresh()
        cached = self.cache.lookup(
            "KA25AB1234"
        )

        self.assertEqual(
            cached.owner_name,
            "Rahul Nayak",
        )
        self.assertEqual(
            cached.owner_email,
            "rahul@example.com",
        )
        self.assertEqual(
            cached.owner_phone,
            "9876543210",
        )
        self.assertEqual(
            cached.owner_type,
            Vehicle.OwnerType.STUDENT,
        )
        self.assertEqual(
            cached.department_id,
            self.department.id,
        )
        self.assertEqual(
            cached.department_code,
            Department.Code.CSE,
        )
        self.assertEqual(
            cached.department_name,
            "Computer Science Engineering",
        )
        self.assertEqual(
            cached.vehicle_company,
            "Honda",
        )
        self.assertEqual(
            cached.vehicle_model,
            "City",
        )
        self.assertEqual(
            cached.color,
            "White",
        )

    def test_authorized_lookup_uses_cached_validity(self):
        self.cache.refresh()

        with CaptureQueriesContext(
            connection
        ) as queries:
            result = self.cache.lookup_result(
                "KA25AB1234",
                on_date=self.today,
            )

        self.assertEqual(
            len(queries),
            0,
        )
        self.assertTrue(result.found)
        self.assertTrue(result.authorized)
        self.assertEqual(
            result.authorization_status,
            Vehicle.AuthorizationStatus.AUTHORIZED,
        )
        self.assertEqual(
            result.vehicle.id,
            self.vehicle.id,
        )

    def test_unknown_plate_is_reported_without_database_query(self):
        self.cache.refresh()

        with CaptureQueriesContext(
            connection
        ) as queries:
            result = self.cache.lookup_result(
                "KA01ZZ9999"
            )

        self.assertEqual(
            len(queries),
            0,
        )
        self.assertFalse(result.found)
        self.assertFalse(result.authorized)
        self.assertEqual(
            result.authorization_status,
            VehicleCache.UNKNOWN_STATUS,
        )
        self.assertIsNone(result.vehicle)

    def test_effective_status_handles_future_and_expired_dates(self):
        self.cache.refresh()

        before_validity = self.cache.lookup_result(
            "KA25AB1234",
            on_date=(
                self.vehicle.valid_from
                - timedelta(days=1)
            ),
        )
        after_expiry = self.cache.lookup_result(
            "KA25AB1234",
            on_date=(
                self.vehicle.valid_until
                + timedelta(days=1)
            ),
        )

        self.assertFalse(
            before_validity.authorized
        )
        self.assertEqual(
            before_validity.authorization_status,
            Vehicle.AuthorizationStatus.PENDING,
        )
        self.assertFalse(
            after_expiry.authorized
        )
        self.assertEqual(
            after_expiry.authorization_status,
            Vehicle.AuthorizationStatus.EXPIRED,
        )

    def test_database_authorization_status_is_preserved(self):
        self.cache.refresh()

        self.vehicle.authorization_status = (
            Vehicle.AuthorizationStatus.UNAUTHORIZED
        )
        self.vehicle.save(
            update_fields=[
                "authorization_status",
                "updated_at",
            ]
        )
        self.cache.upsert(self.vehicle)

        result = self.cache.lookup_result(
            "KA25AB1234",
            on_date=self.today,
        )

        self.assertTrue(result.found)
        self.assertFalse(result.authorized)
        self.assertEqual(
            result.authorization_status,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )
        self.assertEqual(
            self.cache.stats().updates,
            1,
        )

    def test_upsert_replaces_changed_registration_number(self):
        self.cache.refresh()

        self.vehicle.registration_number = (
            "KA25AB9999"
        )
        self.vehicle.save(
            update_fields=[
                "registration_number",
                "updated_at",
            ]
        )

        snapshot = self.cache.upsert(
            self.vehicle
        )

        self.assertEqual(
            snapshot.registration_number,
            "KA25AB9999",
        )
        self.assertIsNone(
            self.cache.lookup(
                "KA25AB1234"
            )
        )
        self.assertIsNotNone(
            self.cache.lookup(
                "KA25AB9999"
            )
        )
        self.assertEqual(
            self.cache.stats().vehicle_count,
            1,
        )

    def test_remove_supports_id_and_registration_number(self):
        self.cache.refresh()

        self.assertTrue(
            self.cache.remove(
                vehicle_id=self.vehicle.id,
            )
        )
        self.assertFalse(
            self.cache.remove(
                vehicle_id=self.vehicle.id,
            )
        )

        self.cache.upsert(self.vehicle)

        self.assertTrue(
            self.cache.remove(
                registration_number="ka 25-ab 1234",
            )
        )
        self.assertEqual(
            self.cache.stats().vehicle_count,
            0,
        )
        self.assertEqual(
            self.cache.stats().removals,
            2,
        )

        with self.assertRaises(ValueError):
            self.cache.remove()

    def test_failed_refresh_preserves_existing_cache(self):
        self.cache.refresh()

        duplicate = Vehicle(
            pk=999,
            owner_name="Duplicate Owner",
            owner_type=Vehicle.OwnerType.STAFF,
            department=self.department,
            vehicle_company="Duplicate",
            vehicle_model="Duplicate",
            vehicle_type=Vehicle.VehicleType.FOUR_WHEELER,
            color="Black",
            fuel_type=Vehicle.FuelType.PETROL,
            registration_number="KA25AB1234",
            registration_date=self.today,
            valid_from=self.today,
            valid_until=(
                self.today + timedelta(days=30)
            ),
            authorization_status=(
                Vehicle.AuthorizationStatus.AUTHORIZED
            ),
        )
        duplicate.updated_at = timezone.now()

        with self.assertRaises(
            VehicleCacheError
        ):
            self.cache.refresh(
                [
                    self.vehicle,
                    duplicate,
                ]
            )

        self.assertEqual(
            self.cache.stats().vehicle_count,
            1,
        )
        self.assertIsNotNone(
            self.cache.lookup(
                "KA25AB1234"
            )
        )
        self.assertIn(
            "Duplicate normalized",
            self.cache.stats().last_error,
        )

    def test_clear_and_ensure_loaded_are_safe(self):
        self.cache.refresh()
        self.cache.clear()

        cleared_stats = self.cache.stats()

        self.assertFalse(cleared_stats.loaded)
        self.assertEqual(
            cleared_stats.vehicle_count,
            0,
        )

        with CaptureQueriesContext(
            connection
        ) as queries:
            count = self.cache.ensure_loaded()

        self.assertEqual(count, 1)
        self.assertEqual(
            len(queries),
            1,
        )
        self.assertTrue(
            self.cache.stats().loaded
        )

class VehicleCacheRefreshServiceTests(SimpleTestCase):
    def make_config(self, **overrides):
        values = {
            "refresh_interval_seconds": 60,
            "retry_interval_seconds": 1,
            "thread_name": "test-cache-refresh",
        }
        values.update(overrides)
        return VehicleCacheSyncConfig(**values)

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            VehicleCacheSyncConfig(
                refresh_interval_seconds=0,
            )

        with self.assertRaises(ValueError):
            VehicleCacheSyncConfig(
                retry_interval_seconds=0,
            )

        with self.assertRaises(ValueError):
            VehicleCacheSyncConfig(
                thread_name=" ",
            )

    def test_start_warms_cache_once(self):
        calls = []

        def loader():
            calls.append("refresh")
            return 2

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=loader,
        )

        try:
            self.assertTrue(service.start())

            stats = service.stats()

            self.assertTrue(stats.running)
            self.assertEqual(calls, ["refresh"])
            self.assertEqual(
                stats.refresh_attempts,
                1,
            )
            self.assertEqual(
                stats.refresh_successes,
                1,
            )
            self.assertEqual(
                stats.last_vehicle_count,
                2,
            )
        finally:
            service.stop()

    def test_start_without_warm_does_not_load_immediately(self):
        calls = []

        def loader():
            calls.append("refresh")
            return 1

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=loader,
        )

        try:
            self.assertTrue(
                service.start(warm=False)
            )
            self.assertEqual(calls, [])
            self.assertEqual(
                service.stats().refresh_attempts,
                0,
            )
        finally:
            service.stop()

    def test_duplicate_start_is_ignored(self):
        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=lambda: 1,
        )

        try:
            self.assertTrue(service.start())
            self.assertFalse(service.start())
            self.assertEqual(
                service.stats().refresh_attempts,
                1,
            )
        finally:
            service.stop()

    def test_initial_refresh_failure_prevents_startup(self):
        def failing_loader():
            raise RuntimeError(
                "database unavailable"
            )

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=failing_loader,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "database unavailable",
        ):
            service.start()

        stats = service.stats()

        self.assertFalse(stats.running)
        self.assertEqual(
            stats.refresh_failures,
            1,
        )
        self.assertIn(
            "database unavailable",
            stats.last_error,
        )
        self.assertTrue(service.stop())

    def test_periodic_background_refresh_runs(self):
        calls = []
        second_refresh = threading.Event()

        def loader():
            calls.append(time.monotonic())

            if len(calls) >= 2:
                second_refresh.set()

            return len(calls)

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(
                refresh_interval_seconds=0.01,
                retry_interval_seconds=0.01,
            ),
            loader=loader,
        )

        try:
            service.start()

            self.assertTrue(
                second_refresh.wait(1)
            )
            self.assertGreaterEqual(
                service.stats().refresh_successes,
                2,
            )
        finally:
            service.stop()

    def test_background_failure_uses_retry_interval(self):
        calls = []
        successful_retry = threading.Event()

        def loader():
            calls.append(time.monotonic())

            if len(calls) == 1:
                raise RuntimeError(
                    "temporary failure"
                )

            successful_retry.set()
            return 3

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(
                refresh_interval_seconds=0.01,
                retry_interval_seconds=0.01,
            ),
            loader=loader,
        )

        try:
            service.start(warm=False)

            self.assertTrue(
                successful_retry.wait(1)
            )

            stats = service.stats()

            self.assertEqual(
                stats.refresh_failures,
                1,
            )
            self.assertGreaterEqual(
                stats.refresh_successes,
                1,
            )
            self.assertEqual(
                stats.last_vehicle_count,
                3,
            )
            self.assertEqual(
                stats.last_error,
                "",
            )
        finally:
            service.stop()

    def test_negative_loader_count_is_rejected(self):
        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=lambda: -1,
        )

        with self.assertRaisesRegex(
            ValueError,
            "negative count",
        ):
            service.refresh_now()

        stats = service.stats()

        self.assertEqual(
            stats.refresh_attempts,
            1,
        )
        self.assertEqual(
            stats.refresh_failures,
            1,
        )

    def test_stop_interrupts_long_wait(self):
        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(
                refresh_interval_seconds=60,
            ),
            loader=lambda: 1,
        )
        service.start(warm=False)

        started = time.perf_counter()
        stopped = service.stop(timeout=1)
        elapsed = time.perf_counter() - started

        self.assertTrue(stopped)
        self.assertLess(elapsed, 0.5)
        self.assertFalse(
            service.stats().running
        )

    def test_service_can_restart_after_clean_stop(self):
        calls = []

        def loader():
            calls.append("refresh")
            return 2

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=loader,
        )

        self.assertTrue(service.start())
        self.assertTrue(service.stop())
        self.assertTrue(service.start())
        self.assertTrue(service.stop())

        self.assertEqual(
            len(calls),
            2,
        )
        self.assertEqual(
            service.stats().refresh_successes,
            2,
        )

    def test_concurrent_manual_refreshes_are_serialized(self):
        active_loaders = 0
        maximum_active_loaders = 0
        guard = threading.Lock()

        def loader():
            nonlocal active_loaders
            nonlocal maximum_active_loaders

            with guard:
                active_loaders += 1
                maximum_active_loaders = max(
                    maximum_active_loaders,
                    active_loaders,
                )

            time.sleep(0.01)

            with guard:
                active_loaders -= 1

            return 1

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=loader,
        )

        threads = [
            threading.Thread(
                target=service.refresh_now,
            )
            for _ in range(5)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(1)
            self.assertFalse(
                thread.is_alive()
            )

        self.assertEqual(
            maximum_active_loaders,
            1,
        )
        self.assertEqual(
            service.stats().refresh_successes,
            5,
        )

    def test_success_clears_previous_error(self):
        outcomes = [
            RuntimeError("first failure"),
            4,
        ]

        def loader():
            outcome = outcomes.pop(0)

            if isinstance(
                outcome,
                BaseException,
            ):
                raise outcome

            return outcome

        service = VehicleCacheRefreshService(
            cache=VehicleCache(),
            config=self.make_config(),
            loader=loader,
        )

        with self.assertRaises(RuntimeError):
            service.refresh_now()

        count = service.refresh_now()
        stats = service.stats()

        self.assertEqual(count, 4)
        self.assertEqual(
            stats.refresh_failures,
            1,
        )
        self.assertEqual(
            stats.refresh_successes,
            1,
        )
        self.assertEqual(
            stats.last_error,
            "",
        )

class FakeProcessorCache:
    def __init__(
        self,
        *,
        loaded=True,
        results=None,
    ):
        self.loaded = loaded
        self.results = results or {}
        self.lookups = []

    def stats(self):
        return SimpleNamespace(
            loaded=self.loaded,
        )

    def lookup_result(self, plate_text):
        self.lookups.append(plate_text)

        if plate_text in self.results:
            return self.results[plate_text]

        return VehicleLookupResult(
            plate_text=plate_text,
            found=False,
            authorized=False,
            authorization_status="UNKNOWN",
            vehicle=None,
        )


class VehicleProcessorTests(SimpleTestCase):
    def make_config(self, **overrides):
        values = {
            "gate_id": 1,
            "direction": "ENTRY",
            "recorded_by_id": 1,
            "required_unknown_votes": 2,
            "maximum_candidates": 3,
            "single_unknown_confidence": 0.95,
        }
        values.update(overrides)
        return VehicleProcessorConfig(**values)

    def make_candidate(
        self,
        frame_index,
        *,
        track_id=12,
        confidence=0.9,
        quality_score=10.0,
    ):
        return VehicleFrameCandidate(
            track_id=track_id,
            frame_index=frame_index,
            captured_at=float(frame_index),
            vehicle_type="car",
            vehicle_confidence=confidence,
            source_bbox=(10, 20, 170, 100),
            sharpness=100.0,
            quality_score=quality_score,
            crop=np.full(
                (80, 160, 3),
                frame_index + 10,
                dtype=np.uint8,
            ),
        )

    def make_track(
        self,
        count=3,
        *,
        track_id=12,
    ):
        return FinalizedVehicleTrack(
            track_id=track_id,
            vehicle_type="car",
            physical_direction="A_TO_B",
            crossing_frame_index=20,
            created_at=time.monotonic(),
            candidates=tuple(
                self.make_candidate(
                    index,
                    track_id=track_id,
                    quality_score=10.0 - index,
                )
                for index in range(count)
            ),
        )

    def make_observation(
        self,
        candidate,
        plate_text,
        confidence=0.8,
    ):
        return PlateObservation(
            plate_text=plate_text,
            raw_text=plate_text,
            confidence=confidence,
            plate_yolo_confidence=0.85,
            ocr_confidence=0.80,
            corrections=0,
            bounding_box=(20, 30, 120, 60),
            plate_image_bytes=(
                f"plate-{candidate.frame_index}"
                .encode()
            ),
            candidate=candidate,
        )

    def make_saver(self, payloads):
        def saver(payload):
            payloads.append(payload)
            return 500 + len(payloads)

        return saver

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            self.make_config(gate_id=0)

        with self.assertRaises(ValueError):
            self.make_config(
                direction="INVALID",
            )

        with self.assertRaises(ValueError):
            self.make_config(
                required_unknown_votes=4,
                maximum_candidates=3,
            )

        with self.assertRaises(ValueError):
            self.make_config(
                duplicate_seconds=0,
            )

    def test_unloaded_cache_fails_closed(self):
        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(
                loaded=False,
            ),
            plate_recognizer=lambda candidate: None,
            record_saver=lambda payload: 1,
        )

        with self.assertRaisesRegex(
            VehicleCacheError,
            "cache is not loaded",
        ):
            processor.process(
                self.make_track()
            )

    def test_no_valid_plate_does_not_save(self):
        payloads = []
        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: None,
            record_saver=self.make_saver(
                payloads
            ),
        )

        result = processor.process(
            self.make_track()
        )

        self.assertFalse(result.saved)
        self.assertEqual(
            result.reason,
            "NO_VALID_PLATE",
        )
        self.assertEqual(
            result.candidates_attempted,
            3,
        )
        self.assertEqual(payloads, [])

    def test_disagreeing_unknown_plates_are_rejected(self):
        plates = {
            0: "KA02MN1826",
            1: "KA02MN1828",
            2: "KA02HN1828",
        }

        def recognizer(candidate):
            return self.make_observation(
                candidate,
                plates[candidate.frame_index],
                confidence=0.80,
            )

        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=recognizer,
            record_saver=lambda payload: 1,
        )

        result = processor.process(
            self.make_track()
        )

        self.assertFalse(result.saved)
        self.assertEqual(
            result.reason,
            "CONSENSUS_NOT_REACHED",
        )
        self.assertEqual(result.votes, 1)

    def test_unknown_consensus_runs_enrichment_once(self):
        payloads = []
        color_calls = []
        make_model_calls = []

        def recognizer(candidate):
            plate = (
                "KA02MN1828"
                if candidate.frame_index < 2
                else "KA02HN1828"
            )
            return self.make_observation(
                candidate,
                plate,
                confidence=0.82,
            )

        def color_detector(crop):
            color_calls.append(crop)
            return "Blue", 0.75

        def make_model_detector(crop):
            make_model_calls.append(crop)
            return {
                "company": "Tata",
                "model": "Nexon",
                "confidence": 0.81,
            }

        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=recognizer,
            color_detector=color_detector,
            make_model_detector=(
                make_model_detector
            ),
            record_saver=self.make_saver(
                payloads
            ),
        )

        result = processor.process(
            self.make_track()
        )
        payload = payloads[0]

        self.assertTrue(result.saved)
        self.assertEqual(
            result.plate_text,
            "KA02MN1828",
        )
        self.assertEqual(result.votes, 2)
        self.assertFalse(result.authorized)
        self.assertEqual(
            result.authorization_status,
            "UNKNOWN",
        )
        self.assertEqual(len(color_calls), 1)
        self.assertEqual(
            len(make_model_calls),
            1,
        )
        self.assertEqual(
            payload.vehicle_color,
            "Blue",
        )
        self.assertEqual(
            payload.detected_vehicle_company,
            "Tata",
        )
        self.assertEqual(
            payload.detected_vehicle_model,
            "Nexon",
        )
        self.assertEqual(
            payload.detected_vehicle_type,
            "Car",
        )
        self.assertIn(
            "computer vision enrichment",
            payload.notes,
        )

    def test_single_unknown_requires_high_confidence(self):
        low_processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: (
                self.make_observation(
                    candidate,
                    "KA02MN1828",
                    confidence=0.90,
                )
            ),
            record_saver=lambda payload: 1,
        )
        high_payloads = []
        high_processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: (
                self.make_observation(
                    candidate,
                    "KA02MN1828",
                    confidence=0.96,
                )
            ),
            record_saver=self.make_saver(
                high_payloads
            ),
        )

        low_result = low_processor.process(
            self.make_track(count=1)
        )
        high_result = high_processor.process(
            self.make_track(count=1)
        )

        self.assertFalse(low_result.saved)
        self.assertEqual(
            low_result.reason,
            "CONSENSUS_NOT_REACHED",
        )
        self.assertTrue(high_result.saved)
        self.assertEqual(high_result.votes, 1)
        self.assertEqual(
            len(high_payloads),
            1,
        )

    def test_registered_fast_path_skips_remaining_ai(self):
        for status, authorized in (
            (
                Vehicle.AuthorizationStatus.AUTHORIZED,
                True,
            ),
            (
                Vehicle.AuthorizationStatus.UNAUTHORIZED,
                False,
            ),
        ):
            with self.subTest(status=status):
                cached_vehicle = SimpleNamespace(
                    id=25,
                    vehicle_type="FOUR_WHEELER",
                    color="White",
                    vehicle_company="Honda",
                    vehicle_model="City",
                )
                cache = FakeProcessorCache(
                    results={
                        "KA25AB1234": VehicleLookupResult(
                            plate_text="KA25AB1234",
                            found=True,
                            authorized=authorized,
                            authorization_status=status,
                            vehicle=cached_vehicle,
                        )
                    }
                )
                recognition_calls = []
                color_calls = []
                make_model_calls = []
                payloads = []

                def recognizer(candidate):
                    recognition_calls.append(
                        candidate.frame_index
                    )
                    return self.make_observation(
                        candidate,
                        "KA25AB1234",
                        confidence=0.80,
                    )

                processor = VehicleProcessor(
                    config=self.make_config(),
                    cache=cache,
                    plate_recognizer=recognizer,
                    color_detector=lambda crop: (
                        color_calls.append(crop)
                    ),
                    make_model_detector=lambda crop: (
                        make_model_calls.append(crop)
                    ),
                    record_saver=self.make_saver(
                        payloads
                    ),
                )

                result = processor.process(
                    self.make_track()
                )
                payload = payloads[0]

                self.assertTrue(result.saved)
                self.assertEqual(
                    result.candidates_attempted,
                    1,
                )
                self.assertEqual(
                    recognition_calls,
                    [0],
                )
                self.assertEqual(color_calls, [])
                self.assertEqual(
                    make_model_calls,
                    [],
                )
                self.assertEqual(
                    result.authorized,
                    authorized,
                )
                self.assertEqual(
                    payload.cached_vehicle.id,
                    25,
                )
                self.assertEqual(
                    payload.vehicle_color,
                    "White",
                )
                self.assertEqual(
                    payload.detected_vehicle_company,
                    "Honda",
                )
                self.assertEqual(
                    payload.detected_vehicle_model,
                    "City",
                )
                self.assertIn(
                    "registered vehicle cache",
                    payload.notes,
                )

    def test_duplicate_plate_is_not_enriched_or_saved_twice(self):
        payloads = []
        enrichment_calls = []

        processor = VehicleProcessor(
            config=self.make_config(
                required_unknown_votes=1,
            ),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: (
                self.make_observation(
                    candidate,
                    "KA02MN1828",
                    confidence=0.80,
                )
            ),
            color_detector=lambda crop: (
                enrichment_calls.append("color")
                or ("Black", 0.8)
            ),
            make_model_detector=lambda crop: (
                enrichment_calls.append("model")
                or {
                    "company": "Unknown",
                    "model": "Unknown",
                    "confidence": 0.0,
                }
            ),
            record_saver=self.make_saver(
                payloads
            ),
        )

        first = processor.process(
            self.make_track(
                count=1,
                track_id=12,
            )
        )
        second = processor.process(
            self.make_track(
                count=1,
                track_id=13,
            )
        )

        self.assertTrue(first.saved)
        self.assertFalse(second.saved)
        self.assertEqual(
            second.reason,
            "DUPLICATE_IGNORED",
        )
        self.assertEqual(
            second.record_id,
            first.record_id,
        )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            enrichment_calls,
            ["color", "model"],
        )

    def test_failed_record_save_releases_reservation(self):
        save_attempts = []

        def saver(payload):
            save_attempts.append(payload)

            if len(save_attempts) == 1:
                raise RuntimeError(
                    "database write failed"
                )

            return 700

        processor = VehicleProcessor(
            config=self.make_config(
                required_unknown_votes=1,
            ),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: (
                self.make_observation(
                    candidate,
                    "KA02MN1828",
                    confidence=0.85,
                )
            ),
            record_saver=saver,
        )
        track = self.make_track(count=1)

        with self.assertRaisesRegex(
            RuntimeError,
            "database write failed",
        ):
            processor.process(track)

        retry = processor.process(track)

        self.assertTrue(retry.saved)
        self.assertEqual(
            retry.record_id,
            700,
        )
        self.assertEqual(
            len(save_attempts),
            2,
        )

    def test_recognizer_failure_does_not_stop_other_candidates(self):
        def recognizer(candidate):
            if candidate.frame_index == 0:
                raise RuntimeError(
                    "bad crop"
                )

            return self.make_observation(
                candidate,
                "KA02MN1828",
                confidence=0.80,
            )

        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=recognizer,
            record_saver=lambda payload: 800,
        )

        result = processor.process(
            self.make_track()
        )

        self.assertTrue(result.saved)
        self.assertEqual(result.votes, 2)
        self.assertEqual(
            result.candidates_attempted,
            3,
        )

    def test_maximum_candidate_limit_is_respected(self):
        calls = []

        def recognizer(candidate):
            calls.append(
                candidate.frame_index
            )
            return self.make_observation(
                candidate,
                "KA02MN1828",
                confidence=0.80,
            )

        processor = VehicleProcessor(
            config=self.make_config(
                maximum_candidates=2,
                required_unknown_votes=2,
            ),
            cache=FakeProcessorCache(),
            plate_recognizer=recognizer,
            record_saver=lambda payload: 900,
        )

        result = processor.process(
            self.make_track(count=3)
        )

        self.assertTrue(result.saved)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(
            result.candidates_attempted,
            2,
        )

    def test_consensus_uses_best_matching_observation(self):
        payloads = []

        def recognizer(candidate):
            confidence = (
                0.70
                if candidate.frame_index == 0
                else 0.88
            )
            return self.make_observation(
                candidate,
                "KA02MN1828",
                confidence=confidence,
            )

        processor = VehicleProcessor(
            config=self.make_config(),
            cache=FakeProcessorCache(),
            plate_recognizer=recognizer,
            record_saver=self.make_saver(
                payloads
            ),
        )

        result = processor.process(
            self.make_track(count=2)
        )

        self.assertTrue(result.saved)
        self.assertEqual(
            result.confidence,
            0.88,
        )
        self.assertEqual(
            payloads[0].plate_image_bytes,
            b"plate-1",
        )

    def test_duplicate_guard_is_scoped_and_token_safe(self):
        guard = RecentPlateGuard(
            cooldown_seconds=5,
        )

        first = guard.reserve(
            gate_id=1,
            direction="ENTRY",
            plate_text="KA25AB1234",
        )
        duplicate = guard.reserve(
            gate_id=1,
            direction="ENTRY",
            plate_text="KA25AB1234",
        )
        other_direction = guard.reserve(
            gate_id=1,
            direction="EXIT",
            plate_text="KA25AB1234",
        )
        other_gate = guard.reserve(
            gate_id=2,
            direction="ENTRY",
            plate_text="KA25AB1234",
        )
        invalid_token = PlateReservation(
            accepted=True,
            key=first.key,
            token="wrong-token",
        )

        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertTrue(
            other_direction.accepted
        )
        self.assertTrue(other_gate.accepted)
        self.assertFalse(
            guard.release(
                reservation=invalid_token,
            )
        )
        self.assertFalse(
            guard.reserve(
                gate_id=1,
                direction="ENTRY",
                plate_text="KA25AB1234",
            ).accepted
        )
        self.assertTrue(
            guard.release(
                reservation=first,
            )
        )
        self.assertTrue(
            guard.reserve(
                gate_id=1,
                direction="ENTRY",
                plate_text="KA25AB1234",
            ).accepted
        )


class VehicleProcessorRecordSaveTests(TestCase):
    def setUp(self):
        self.gate = Gate.objects.create(
            name="Processor Test Gate",
            gate_type=Gate.GateType.ENTRY,
        )
        self.user = User.objects.create_user(
            username="processor-user",
            password="test-password",
        )
        self.processor = VehicleProcessor(
            config=VehicleProcessorConfig(
                gate_id=self.gate.id,
                direction="ENTRY",
                recorded_by_id=self.user.id,
            ),
            cache=FakeProcessorCache(),
            plate_recognizer=lambda candidate: None,
        )

    def test_default_saver_maps_payload_to_existing_record_model(self):
        payload = VehicleRecordPayload(
            track_id=44,
            plate_text="KA02MN1828",
            confidence=0.82,
            authorization_status="UNKNOWN",
            was_authorized=False,
            cached_vehicle=None,
            detected_vehicle_type="Car",
            vehicle_type_confidence=0.91,
            vehicle_color="Blue",
            vehicle_color_confidence=0.75,
            detected_vehicle_company="Tata",
            detected_vehicle_model="Nexon",
            vehicle_make_model_confidence=0.81,
            notes="Track ID: 44; test record.",
            captured_image_bytes=None,
            plate_image_bytes=None,
        )

        record_id = self.processor._save_record(
            payload
        )
        record = EntryExitRecord.objects.get(
            pk=record_id
        )

        self.assertEqual(
            record.detected_plate_text,
            "KA02MN1828",
        )
        self.assertEqual(
            record.direction,
            "ENTRY",
        )
        self.assertEqual(
            record.gate_id,
            self.gate.id,
        )
        self.assertEqual(
            record.recorded_by_id,
            self.user.id,
        )
        self.assertFalse(
            record.was_authorized
        )
        self.assertEqual(
            record.detection_source,
            EntryExitRecord.DetectionSource.CCTV,
        )
        self.assertEqual(
            record.detected_vehicle_type,
            "Car",
        )
        self.assertEqual(
            record.vehicle_color,
            "Blue",
        )
        self.assertEqual(
            record.detected_vehicle_company,
            "Tata",
        )
        self.assertEqual(
            record.detected_vehicle_model,
            "Nexon",
        )

class FakePipelineCacheService:
    def __init__(
        self,
        events=None,
        start_error=None,
    ):
        self.events = (
            events if events is not None else []
        )
        self.start_error = start_error
        self.running = False
        self.stop_calls = []

    def start(self, *, warm=True):
        self.events.append(
            ("cache-start", warm)
        )

        if self.start_error:
            raise self.start_error

        self.running = True
        return True

    def stop(self, timeout=5.0):
        self.events.append(
            ("cache-stop", timeout)
        )
        self.stop_calls.append(timeout)
        self.running = False
        return True


class FakePipelineWorkerPool:
    def __init__(
        self,
        *,
        events=None,
        accepts=True,
        start_error=None,
    ):
        self.events = (
            events if events is not None else []
        )
        self.accepts = accepts
        self.start_error = start_error
        self.running = False
        self.submitted = []
        self.stop_calls = []

    def start(self):
        self.events.append("worker-start")

        if self.start_error:
            raise self.start_error

        self.running = True
        return True

    def submit(self, task):
        self.submitted.append(task)
        return self.accepts

    def stop(
        self,
        *,
        drain=True,
        timeout=None,
    ):
        self.events.append(
            ("worker-stop", drain, timeout)
        )
        self.stop_calls.append(
            (drain, timeout)
        )
        self.running = False
        return True

    def stats(self):
        return WorkerPoolStats(
            state=(
                WorkerPoolState.RUNNING
                if self.running
                else WorkerPoolState.STOPPED
            ),
            submitted=len(self.submitted),
            completed=0,
            failed=0,
            rejected_full=(
                0 if self.accepts else 1
            ),
            rejected_not_running=0,
            discarded_on_stop=0,
            queue_size=0,
            queue_capacity=100,
            in_flight=0,
            live_workers=(
                1 if self.running else 0
            ),
            last_error="",
        )


class FakePipelineTracker:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def track(self, frame):
        self.calls.append(frame)

        if len(self.results) > 1:
            return self.results.pop(0)

        return self.results[0]


class FakePipelineLineDetector:
    def __init__(self, events=None):
        self.events = events or {}
        self.calls = []

    def update(
        self,
        *,
        track_id,
        center,
        frame_width,
        frame_height,
        frame_index,
    ):
        self.calls.append(
            (
                track_id,
                center,
                frame_index,
            )
        )
        return self.events.get(
            (track_id, frame_index)
        )


class FakePipelineCandidateBuffer:
    def __init__(self, tasks=None):
        self.tasks = tasks or {}
        self.observations = []
        self.finalize_calls = []

    def observe(
        self,
        *,
        frame,
        detection,
        frame_index,
        captured_at,
    ):
        self.observations.append(
            (
                detection.track_id,
                frame_index,
            )
        )
        return True

    def finalize(self, crossing):
        self.finalize_calls.append(
            crossing.track_id
        )
        return self.tasks.get(
            crossing.track_id
        )


class CameraTrackingPipelineTests(SimpleTestCase):
    def make_gate(self, **overrides):
        values = {
            "pk": 1,
            "gate_type": "ENTRY",
            "line_start_x": 0.1,
            "line_start_y": 0.5,
            "line_end_x": 0.9,
            "line_end_y": 0.5,
            "crossing_direction": "ANY",
            "line_crossing_enabled": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def make_detection(
        self,
        *,
        track_id=12,
        vehicle_type="car",
        bbox=(30, 120, 130, 180),
    ):
        return VehicleDetection(
            track_id=track_id,
            class_id=2,
            vehicle_type=vehicle_type,
            confidence=0.9,
            bbox=bbox,
        )

    def make_tracking_result(
        self,
        detections,
        *,
        inference_ms=5.0,
    ):
        return VehicleTrackingResult(
            detections=tuple(detections),
            frame_width=300,
            frame_height=200,
            inference_ms=inference_ms,
        )

    def make_crossing(
        self,
        track_id=12,
        frame_index=1,
    ):
        return LineCrossingEvent(
            track_id=track_id,
            physical_direction="A_TO_B",
            previous_point=NormalizedPoint(
                0.4,
                0.7,
            ),
            current_point=NormalizedPoint(
                0.4,
                0.3,
            ),
            intersection_point=NormalizedPoint(
                0.4,
                0.5,
            ),
            frame_index=frame_index,
        )

    def make_task(
        self,
        track_id=12,
    ):
        candidate = VehicleFrameCandidate(
            track_id=track_id,
            frame_index=0,
            captured_at=0.0,
            vehicle_type="car",
            vehicle_confidence=0.9,
            source_bbox=(30, 120, 130, 180),
            sharpness=10.0,
            quality_score=10.0,
            crop=np.zeros(
                (60, 100, 3),
                dtype=np.uint8,
            ),
        )

        return FinalizedVehicleTrack(
            track_id=track_id,
            vehicle_type="car",
            physical_direction="A_TO_B",
            crossing_frame_index=1,
            created_at=time.monotonic(),
            candidates=(candidate,),
        )

    def make_pipeline(
        self,
        *,
        tracker=None,
        line_detector=None,
        candidate_buffer=None,
        cache_service=None,
        worker_pool=None,
        gate=None,
        on_activity=None,
        on_error=None,
    ):
        tracker = tracker or FakePipelineTracker(
            [
                self.make_tracking_result(
                    []
                )
            ]
        )
        line_detector = (
            line_detector
            or FakePipelineLineDetector()
        )
        candidate_buffer = (
            candidate_buffer
            or FakePipelineCandidateBuffer()
        )
        cache_service = (
            cache_service
            or FakePipelineCacheService()
        )
        worker_pool = (
            worker_pool
            or FakePipelineWorkerPool()
        )

        return CameraTrackingPipeline(
            gate=gate or self.make_gate(),
            recorded_by_id=1,
            cache=VehicleCache(),
            tracker=tracker,
            line_detector=line_detector,
            candidate_buffer=candidate_buffer,
            cache_service=cache_service,
            worker_pool=worker_pool,
            on_activity=on_activity,
            on_error=on_error,
        )

    def test_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            TrackingPipelineConfig(
                worker_count=0,
            )

        with self.assertRaises(ValueError):
            TrackingPipelineConfig(
                vehicle_queue_size=0,
            )

        with self.assertRaises(ValueError):
            TrackingPipelineConfig(
                candidates_per_track=2,
                required_unknown_votes=3,
            )

        with self.assertRaises(ValueError):
            TrackingPipelineConfig(
                cache_refresh_seconds=0,
            )

    def test_gate_and_user_are_validated(self):
        with self.assertRaises(ValueError):
            CameraTrackingPipeline(
                gate=self.make_gate(pk=None),
                recorded_by_id=1,
            )

        with self.assertRaises(ValueError):
            CameraTrackingPipeline(
                gate=self.make_gate(),
                recorded_by_id=0,
            )

    def test_start_order_and_duplicate_start(self):
        events = []
        cache_service = (
            FakePipelineCacheService(
                events=events
            )
        )
        worker_pool = FakePipelineWorkerPool(
            events=events
        )
        pipeline = self.make_pipeline(
            cache_service=cache_service,
            worker_pool=worker_pool,
        )

        try:
            self.assertTrue(pipeline.start())
            self.assertFalse(pipeline.start())
            self.assertEqual(
                events[:2],
                [
                    ("cache-start", True),
                    "worker-start",
                ],
            )
            self.assertTrue(
                pipeline.stats().running
            )
        finally:
            pipeline.stop()

    def test_worker_start_failure_stops_cache_service(self):
        events = []
        cache_service = (
            FakePipelineCacheService(
                events=events
            )
        )
        worker_pool = FakePipelineWorkerPool(
            events=events,
            start_error=RuntimeError(
                "workers unavailable"
            ),
        )
        pipeline = self.make_pipeline(
            cache_service=cache_service,
            worker_pool=worker_pool,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "workers unavailable",
        ):
            pipeline.start()

        self.assertIn(
            ("cache-stop", 5.0),
            events,
        )
        self.assertFalse(
            pipeline.stats().running
        )

    def test_frame_before_start_is_rejected(self):
        pipeline = self.make_pipeline()

        with self.assertRaisesRegex(
            RuntimeError,
            "not running",
        ):
            pipeline.process_frame(
                frame=np.zeros(
                    (200, 300, 3),
                    dtype=np.uint8,
                ),
                frame_index=0,
            )

    def test_frame_index_must_increase(self):
        pipeline = self.make_pipeline()
        frame = np.zeros(
            (200, 300, 3),
            dtype=np.uint8,
        )
        pipeline.start()

        try:
            pipeline.process_frame(
                frame=frame,
                frame_index=1,
            )

            with self.assertRaisesRegex(
                ValueError,
                "monotonically",
            ):
                pipeline.process_frame(
                    frame=frame,
                    frame_index=1,
                )
        finally:
            pipeline.stop()

    def test_untracked_vehicle_is_visible_but_not_buffered(self):
        detection = self.make_detection(
            track_id=None,
        )
        tracker = FakePipelineTracker(
            [
                self.make_tracking_result(
                    [detection]
                )
            ]
        )
        line_detector = (
            FakePipelineLineDetector()
        )
        candidate_buffer = (
            FakePipelineCandidateBuffer()
        )
        pipeline = self.make_pipeline(
            tracker=tracker,
            line_detector=line_detector,
            candidate_buffer=candidate_buffer,
        )
        pipeline.start()

        try:
            result = pipeline.process_frame(
                frame=np.zeros(
                    (200, 300, 3),
                    dtype=np.uint8,
                ),
                frame_index=0,
            )

            self.assertEqual(
                result.vehicle_count,
                1,
            )
            self.assertEqual(
                result.tracked_count,
                0,
            )
            self.assertEqual(
                candidate_buffer.observations,
                [],
            )
            self.assertEqual(
                line_detector.calls,
                [],
            )
        finally:
            pipeline.stop()

    def test_crossing_without_candidate_is_reported_rejected(self):
        detection = self.make_detection()
        crossing = self.make_crossing()
        tracker = FakePipelineTracker(
            [
                self.make_tracking_result(
                    [detection]
                )
            ]
        )
        line_detector = (
            FakePipelineLineDetector(
                events={
                    (12, 1): crossing,
                }
            )
        )
        candidate_buffer = (
            FakePipelineCandidateBuffer()
        )
        worker_pool = FakePipelineWorkerPool()
        pipeline = self.make_pipeline(
            tracker=tracker,
            line_detector=line_detector,
            candidate_buffer=candidate_buffer,
            worker_pool=worker_pool,
        )
        pipeline.start()

        try:
            result = pipeline.process_frame(
                frame=np.zeros(
                    (200, 300, 3),
                    dtype=np.uint8,
                ),
                frame_index=1,
            )

            self.assertEqual(
                result.rejected_track_ids,
                (12,),
            )
            self.assertEqual(
                worker_pool.submitted,
                [],
            )
            self.assertEqual(
                pipeline.stats().tasks_rejected,
                1,
            )
        finally:
            pipeline.stop()

    def test_full_vehicle_queue_is_nonblocking_and_counted(self):
        detection = self.make_detection()
        crossing = self.make_crossing()
        task = self.make_task()
        tracker = FakePipelineTracker(
            [
                self.make_tracking_result(
                    [detection]
                )
            ]
        )
        line_detector = (
            FakePipelineLineDetector(
                events={
                    (12, 1): crossing,
                }
            )
        )
        candidate_buffer = (
            FakePipelineCandidateBuffer(
                tasks={
                    12: task,
                }
            )
        )
        worker_pool = FakePipelineWorkerPool(
            accepts=False
        )
        pipeline = self.make_pipeline(
            tracker=tracker,
            line_detector=line_detector,
            candidate_buffer=candidate_buffer,
            worker_pool=worker_pool,
        )
        pipeline.start()

        try:
            started = time.perf_counter()
            result = pipeline.process_frame(
                frame=np.zeros(
                    (200, 300, 3),
                    dtype=np.uint8,
                ),
                frame_index=1,
            )
            elapsed = (
                time.perf_counter() - started
            )

            self.assertLess(elapsed, 0.2)
            self.assertEqual(
                result.rejected_track_ids,
                (12,),
            )
            self.assertEqual(
                pipeline.stats().tasks_rejected,
                1,
            )
        finally:
            pipeline.stop()

    def test_real_flow_tracks_all_vehicles_and_submits_crossings(self):
        first_detections = [
            self.make_detection(
                track_id=12,
                bbox=(30, 120, 130, 180),
            ),
            self.make_detection(
                track_id=13,
                vehicle_type="motorcycle",
                bbox=(170, 120, 250, 180),
            ),
        ]
        second_detections = [
            self.make_detection(
                track_id=12,
                bbox=(30, 20, 130, 80),
            ),
            self.make_detection(
                track_id=13,
                vehicle_type="motorcycle",
                bbox=(170, 20, 250, 80),
            ),
        ]
        tracker = FakePipelineTracker(
            [
                self.make_tracking_result(
                    first_detections
                ),
                self.make_tracking_result(
                    second_detections
                ),
            ]
        )
        cache = VehicleCache()
        cache.refresh([])
        cache_service = (
            FakePipelineCacheService()
        )
        activities = []

        def processor_factory():
            def process(task):
                return VehicleProcessingResult(
                    track_id=task.track_id,
                    saved=True,
                    reason="SAVED",
                    plate_text="KA25AB1234",
                    record_id=(
                        1000 + task.track_id
                    ),
                )

            return process

        pipeline = CameraTrackingPipeline(
            gate=self.make_gate(),
            recorded_by_id=1,
            config=TrackingPipelineConfig(
                worker_count=2,
                vehicle_queue_size=10,
            ),
            cache=cache,
            tracker=tracker,
            cache_service=cache_service,
            processor_factory=(
                processor_factory
            ),
            on_activity=lambda track, result: (
                activities.append(
                    (track.track_id, result.record_id)
                )
            ),
        )
        frame = np.zeros(
            (200, 300, 3),
            dtype=np.uint8,
        )
        pipeline.start()

        try:
            first = pipeline.process_frame(
                frame=frame,
                frame_index=0,
            )
            second = pipeline.process_frame(
                frame=frame,
                frame_index=1,
            )

            self.assertEqual(
                first.submitted_track_ids,
                (),
            )
            self.assertCountEqual(
                second.submitted_track_ids,
                [12, 13],
            )
            self.assertTrue(
                pipeline.worker_pool.wait_until_idle(
                    2
                )
            )

            stats = pipeline.stats()

            self.assertEqual(
                stats.frames_processed,
                2,
            )
            self.assertEqual(
                stats.vehicles_observed,
                4,
            )
            self.assertEqual(
                stats.tracked_vehicles_observed,
                4,
            )
            self.assertEqual(
                stats.line_crossings,
                2,
            )
            self.assertEqual(
                stats.tasks_submitted,
                2,
            )
            self.assertEqual(
                stats.processing_results,
                2,
            )
            self.assertEqual(
                stats.records_saved,
                2,
            )
            self.assertCountEqual(
                activities,
                [
                    (12, 1012),
                    (13, 1013),
                ],
            )
        finally:
            pipeline.stop()

    def test_worker_callbacks_update_activity_statistics(self):
        activities = []
        errors = []
        pipeline = self.make_pipeline(
            on_activity=lambda track, result: (
                activities.append(result.reason)
            ),
            on_error=lambda track, error: (
                errors.append(str(error))
            ),
        )
        task = self.make_task()

        pipeline._handle_worker_success(
            task,
            VehicleProcessingResult(
                track_id=12,
                saved=True,
                reason="SAVED",
                record_id=1,
            ),
        )
        pipeline._handle_worker_success(
            task,
            VehicleProcessingResult(
                track_id=12,
                saved=False,
                reason="DUPLICATE_IGNORED",
                record_id=1,
            ),
        )
        pipeline._handle_worker_error(
            task,
            RuntimeError("OCR failed"),
        )

        stats = pipeline.stats()

        self.assertEqual(
            stats.processing_results,
            2,
        )
        self.assertEqual(
            stats.records_saved,
            1,
        )
        self.assertEqual(
            stats.duplicate_results,
            1,
        )
        self.assertEqual(
            stats.processing_failures,
            1,
        )
        self.assertEqual(
            activities,
            [
                "SAVED",
                "DUPLICATE_IGNORED",
            ],
        )
        self.assertEqual(
            errors,
            ["OCR failed"],
        )
        self.assertIn(
            "OCR failed",
            stats.last_error,
        )

    def test_stop_passes_drain_and_timeout_to_workers(self):
        cache_service = (
            FakePipelineCacheService()
        )
        worker_pool = FakePipelineWorkerPool()
        pipeline = self.make_pipeline(
            cache_service=cache_service,
            worker_pool=worker_pool,
        )
        pipeline.start()

        stopped = pipeline.stop(
            drain=False,
            timeout=1.5,
        )

        self.assertTrue(stopped)
        self.assertEqual(
            worker_pool.stop_calls,
            [
                (False, 1.5),
            ],
        )
        self.assertEqual(
            cache_service.stop_calls,
            [5.0],
        )
        self.assertFalse(
            pipeline.stats().running
        )