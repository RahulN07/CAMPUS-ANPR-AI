import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from access_management.models import Gate
from accounts.models import User
from anpr.camera_capture import CameraCaptureService
from anpr.detector import detect_plate_bboxes, run_ocr
from anpr.tracking_pipeline import (
    CameraTrackingPipeline,
    TrackingPipelineConfig,
)
from anpr.vehicle_cache import get_vehicle_cache
from anpr.vehicle_processor import (
    RecentPlateGuard,
    VehicleProcessingResult,
    VehicleProcessor,
    VehicleProcessorConfig,
)
from anpr.vehicle_tracker import VehicleTracker, VehicleTrackerConfig
from notifications.models import Notification
from records.models import EntryExitRecord


class Command(BaseCommand):
    help = (
        "Run continuous multi-vehicle CCTV ANPR with YOLOv8 tracking, "
        "line crossing, a bounded frame queue, and parallel OCR workers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=False,
            help=(
                "Optional source override: camera index, video path, "
                "HTTP URL, or RTSP URL. Otherwise Gate configuration is used."
            ),
        )
        parser.add_argument("--gate", type=int, required=True)
        parser.add_argument(
            "--direction",
            choices=["ENTRY", "EXIT"],
            default=None,
            help=(
                "Deprecated compatibility option. It must match the Gate's "
                "configured gate_type."
            ),
        )
        parser.add_argument("--recorded-by", type=int, required=True)
        parser.add_argument(
            "--confidence",
            type=float,
            default=0.40,
            help="Licence-plate YOLO confidence threshold.",
        )
        parser.add_argument(
            "--required-votes",
            type=int,
            default=2,
            help="Matching OCR readings required for an unknown plate.",
        )
        parser.add_argument(
            "--candidates",
            type=int,
            default=3,
            help=(
                "Quality-ranked crops retained per Track ID (1-10). "
                "This can be greater than --required-votes."
            ),
        )
        parser.add_argument(
            "--evaluate-all-unknown-candidates",
            action="store_true",
            help=(
                "Evaluate every retained crop before voting for an "
                "unknown plate. Registered cache hits still use the fast "
                "path."
            ),
        )
        parser.add_argument(
            "--diagnostic-only",
            action="store_true",
            help=(
                "Run the complete pipeline without creating records or "
                "notifications. Intended for camera/OCR calibration."
            ),
        )
        parser.add_argument(
            "--tracker",
            default=None,
            help=(
                "Built-in Ultralytics tracker name or custom YAML path. "
                "Defaults to the Django ANPR_TRACKER_CONFIG setting."
            ),
        )
        parser.add_argument(
            "--cooldown",
            type=float,
            default=5.0,
            help="Duplicate plate cooldown in seconds per gate and direction.",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=5,
            help="Parallel vehicle task workers (default: 5).",
        )
        parser.add_argument(
            "--vehicle-queue-size",
            type=int,
            default=100,
            help="Bounded finalized-vehicle task queue capacity.",
        )
        parser.add_argument(
            "--cache-refresh",
            type=float,
            default=30.0,
            help="Registered-vehicle RAM cache refresh interval in seconds.",
        )
        parser.add_argument(
            "--detection-interval",
            type=float,
            default=None,
            help=(
                "Deprecated. Tracking now processes the newest queued frame "
                "continuously at the Gate target FPS."
            ),
        )
        parser.add_argument(
            "--vote-timeout",
            type=float,
            default=None,
            help="Deprecated. OCR voting is now scoped to each Track ID.",
        )
        parser.add_argument("--show", action="store_true")

    def handle(self, *args, **options):
        gate = self._get_gate(options["gate"])
        recorded_by = self._get_recording_user(options["recorded_by"])
        direction = self._resolve_direction(gate, options.get("direction"))
        source_argument = self.resolve_source_argument(
            gate=gate,
            source_override=options.get("source"),
        )
        source, is_video_file = self.parse_source(source_argument)

        confidence = max(0.0, min(1.0, float(options["confidence"])))
        required_votes = max(1, min(10, int(options["required_votes"])))
        requested_candidates = max(
            1,
            min(10, int(options["candidates"])),
        )
        candidate_count = max(
            requested_candidates,
            required_votes,
        )
        cooldown = max(0.1, float(options["cooldown"]))
        worker_count = max(1, min(32, int(options["workers"])))
        vehicle_queue_size = max(
            1,
            min(10000, int(options["vehicle_queue_size"])),
        )
        cache_refresh = max(1.0, float(options["cache_refresh"]))
        show_preview = bool(options["show"])
        diagnostic_only = bool(options["diagnostic_only"])
        tracker_config_name = str(
            options.get("tracker")
            or getattr(settings, "ANPR_TRACKER_CONFIG", "bytetrack.yaml")
        )
        built_in_trackers = {"bytetrack.yaml", "botsort.yaml"}
        if tracker_config_name not in built_in_trackers:
            tracker_path = Path(tracker_config_name).expanduser()
            if not tracker_path.is_file():
                raise CommandError(
                    f"Tracker configuration does not exist: {tracker_path}"
                )
            tracker_config_name = str(tracker_path.resolve())

        self._output_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._latest_activity: VehicleProcessingResult | None = None
        self._latest_activity_until = 0.0
        self._diagnostic_only = diagnostic_only

        cache = get_vehicle_cache()
        duplicate_guard = RecentPlateGuard(cooldown)
        processor_config = VehicleProcessorConfig(
            gate_id=gate.id,
            direction=direction,
            recorded_by_id=recorded_by.id,
            plate_confidence=confidence,
            required_unknown_votes=required_votes,
            maximum_candidates=candidate_count,
            duplicate_seconds=cooldown,
            evaluate_all_unknown_candidates=bool(
                options["evaluate_all_unknown_candidates"]
            ),
        )

        def processor_factory():
            processor = VehicleProcessor(
                config=processor_config,
                cache=cache,
                duplicate_guard=duplicate_guard,
                record_saver=(
                    (lambda payload: 0)
                    if diagnostic_only
                    else None
                ),
            )
            return processor.process

        pipeline = CameraTrackingPipeline(
            gate=gate,
            recorded_by_id=recorded_by.id,
            config=TrackingPipelineConfig(
                worker_count=worker_count,
                vehicle_queue_size=vehicle_queue_size,
                candidates_per_track=candidate_count,
                required_unknown_votes=required_votes,
                duplicate_seconds=cooldown,
                cache_refresh_seconds=cache_refresh,
            ),
            cache=cache,
            tracker=VehicleTracker(
                config=VehicleTrackerConfig(
                    confidence=float(
                        getattr(
                            settings,
                            "ANPR_VEHICLE_CONFIDENCE",
                            0.35,
                        )
                    ),
                    iou=float(
                        getattr(settings, "ANPR_VEHICLE_IOU", 0.50)
                    ),
                    tracker=tracker_config_name,
                    image_size=int(
                        getattr(settings, "ANPR_VEHICLE_IMAGE_SIZE", 640)
                    ),
                    device=getattr(settings, "ANPR_YOLO_DEVICE", None),
                )
            ),
            processor_factory=processor_factory,
            on_activity=self._handle_activity,
            on_error=self._handle_processing_error,
        )

        capture_service = None
        latest_frame = None
        latest_frame_result = None
        latest_frame_time = None
        smoothed_fps = 0.0

        try:
            pipeline.start()
            # Load weights and execute one dummy inference for every cold AI
            # path before opening the camera. Model construction and oneDNN
            # initialization must not be charged to the first vehicle task.
            vehicle_class_ids = self._warm_ai_models(pipeline)

            capture_service = CameraCaptureService(
                source=source,
                gate_id=gate.id,
                target_fps=gate.target_fps,
                queue_size=30,
                source_name=gate.camera_name or gate.name,
                replay_video_in_real_time=is_video_file,
                reconnect_delay=2.0,
            ).start()

            self._print_startup(
                gate=gate,
                direction=direction,
                source_argument=source_argument,
                worker_count=worker_count,
                vehicle_queue_size=vehicle_queue_size,
                candidate_count=candidate_count,
                required_votes=required_votes,
                cooldown=cooldown,
                cache_refresh=cache_refresh,
                vehicle_class_ids=vehicle_class_ids,
                show_preview=show_preview,
                tracker_config_name=tracker_config_name,
            )

            while True:
                packet = None
                try:
                    packet = capture_service.get_frame(timeout=0.05)
                except queue.Empty:
                    packet = None

                if packet is not None:
                    try:
                        latest_frame = packet.frame
                        captured_at = getattr(packet, "captured_at", None)
                        if not isinstance(captured_at, (int, float)):
                            captured_at = None

                        latest_frame_result = pipeline.process_frame(
                            frame=latest_frame,
                            frame_index=packet.sequence,
                            captured_at=captured_at,
                        )

                        now = time.perf_counter()
                        if latest_frame_time is not None:
                            instantaneous = 1.0 / max(
                                now - latest_frame_time,
                                1e-6,
                            )
                            smoothed_fps = (
                                instantaneous
                                if smoothed_fps <= 0
                                else smoothed_fps * 0.8 + instantaneous * 0.2
                            )
                        latest_frame_time = now
                    except Exception as error:
                        self._write_error(
                            f"Tracking frame failed: {error}"
                        )
                    finally:
                        capture_service.task_done()

                if show_preview and latest_frame is not None:
                    preview = latest_frame.copy()
                    self.draw_tracking_preview(
                        frame=preview,
                        pipeline=pipeline,
                        frame_result=latest_frame_result,
                        fps=smoothed_fps,
                    )
                    cv2.imshow("Campus Security ANPR Tracking", preview)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                capture_stats = capture_service.stats()
                if capture_stats.ended and capture_stats.queue.size == 0:
                    break

                if packet is None:
                    time.sleep(0.005)

        except KeyboardInterrupt:
            self._write_warning("CCTV ANPR stopped by user.")
        finally:
            if capture_service is not None:
                capture_service.stop(timeout=5, clear_queue=True)

            pipeline_stopped = pipeline.stop(drain=True, timeout=60.0)
            cv2.destroyAllWindows()

            if capture_service is not None:
                capture_stats = capture_service.stats()
                self.stdout.write(
                    "Capture summary     : "
                    f"read={capture_stats.frames_read}, "
                    f"queued={capture_stats.frames_enqueued}, "
                    f"dropped={capture_stats.queue.dropped}, "
                    f"reconnects={capture_stats.reconnects}"
                )

            pipeline_stats = pipeline.stats()
            worker_stats = pipeline.worker_pool.stats()
            self.stdout.write(
                "Pipeline summary    : "
                f"frames={pipeline_stats.frames_processed}, "
                f"vehicles={pipeline_stats.vehicles_observed}, "
                f"crossings={pipeline_stats.line_crossings}, "
                f"submitted={pipeline_stats.tasks_submitted}, "
                f"saved={pipeline_stats.records_saved}, "
                f"duplicates={pipeline_stats.duplicate_results}, "
                f"queue_rejected={pipeline_stats.tasks_rejected}, "
                f"worker_failed={worker_stats.failed}"
            )

            if not pipeline_stopped:
                self._write_warning(
                    "Some background workers did not stop before timeout."
                )

            self.stdout.write(
                self.style.SUCCESS("CCTV ANPR process closed.")
            )

    def _get_gate(self, gate_id):
        gate = Gate.objects.filter(id=gate_id).first()
        if gate is None:
            raise CommandError(f"Gate ID {gate_id} does not exist.")
        if not gate.is_active:
            raise CommandError(f"Gate ID {gate_id} is inactive.")
        return gate

    def _get_recording_user(self, user_id):
        user = User.objects.filter(id=user_id, is_active=True).first()
        if user is None:
            raise CommandError(f"Active user ID {user_id} does not exist.")
        return user

    def _resolve_direction(self, gate, requested_direction):
        direction = gate.gate_type
        if requested_direction is not None and requested_direction != direction:
            raise CommandError(
                "--direction conflicts with the selected Gate. "
                f"Gate {gate.id} is configured as {direction}."
            )
        return direction

    def resolve_source_argument(self, gate, source_override=None):
        """Resolve a CLI override or the selected Gate configuration."""

        if source_override is not None:
            source_text = str(source_override).strip()
            if source_text:
                return source_text

        local_sources = {
            Gate.CameraSource.WEBCAM,
            Gate.CameraSource.USB_CAMERA,
        }
        stream_sources = {
            Gate.CameraSource.IP_CAMERA,
            Gate.CameraSource.RTSP,
            Gate.CameraSource.CCTV,
        }

        if gate.camera_source in local_sources:
            return str(gate.camera_device_index)
        if gate.camera_source in stream_sources:
            if not gate.camera_ip:
                raise CommandError(
                    "The selected Gate does not have a camera stream address "
                    "configured."
                )
            return gate.camera_ip.strip()
        if gate.camera_source == Gate.CameraSource.VIDEO_UPLOAD:
            raise CommandError(
                "Uploaded-video gates require --source with a local video "
                "file path."
            )
        raise CommandError(f"Unsupported camera source: {gate.camera_source}")

    def describe_source(self, source_argument):
        """Return a display-safe label without exposing stream credentials."""

        source_text = str(source_argument).strip()
        if source_text.isdigit():
            return f"local camera index {source_text}"
        if source_text.lower().startswith("rtsp://"):
            return "RTSP stream"
        if source_text.lower().startswith(("http://", "https://")):
            return "HTTP camera stream"
        return Path(source_text).name or "video file"

    def parse_source(self, source_argument):
        source_text = str(source_argument).strip()
        if source_text.isdigit():
            return int(source_text), False
        if source_text.lower().startswith(
            ("rtsp://", "http://", "https://")
        ):
            return source_text, False

        source_path = Path(source_text)
        if source_path.exists() and source_path.is_file():
            return str(source_path), True
        raise CommandError(f"Invalid video source: {source_text}")

    def _print_startup(
        self,
        *,
        gate,
        direction,
        source_argument,
        worker_count,
        vehicle_queue_size,
        candidate_count,
        required_votes,
        cooldown,
        cache_refresh,
        vehicle_class_ids,
        show_preview,
        tracker_config_name,
    ):
        self.stdout.write(
            self.style.SUCCESS(
                "Continuous multi-vehicle CCTV ANPR started successfully."
            )
        )
        self.stdout.write(
            f"Source             : {self.describe_source(source_argument)}"
        )
        self.stdout.write(f"Gate               : {gate}")
        self.stdout.write(f"Direction          : {direction}")
        self.stdout.write(f"Capture FPS        : {gate.target_fps}")
        self.stdout.write("Frame queue        : 30 (drop oldest)")
        self.stdout.write(f"Vehicle classes    : {vehicle_class_ids}")
        tracker_label = (
            "BoT-SORT ReID"
            if "reid" in Path(tracker_config_name).stem.lower()
            else (
                "BoT-SORT"
                if Path(tracker_config_name).name == "botsort.yaml"
                else "ByteTrack"
            )
        )
        self.stdout.write(f"Tracker            : {tracker_label}")
        self.stdout.write(f"Vehicle workers    : {worker_count}")
        self.stdout.write(f"Vehicle queue      : {vehicle_queue_size}")
        self.stdout.write(
            f"Candidate crops    : {candidate_count} (quality ranked)"
        )
        self.stdout.write(f"Unknown OCR votes  : {required_votes}")
        self.stdout.write(f"Duplicate cooldown : {cooldown:g} seconds")
        self.stdout.write(f"Cache refresh      : {cache_refresh:g} seconds")
        if show_preview:
            self.stdout.write("Press Q inside the video window to stop.")

    def _warm_ai_models(self, pipeline):
        self.stdout.write(
            "AI warm-up          : vehicle YOLO, plate YOLO, PaddleOCR..."
        )
        started = time.perf_counter()
        blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        blank_plate = np.zeros((96, 320), dtype=np.uint8)

        # VehicleTracker.track() initializes Ultralytics tracking and its LAP
        # association dependency. The empty frame creates no persistent IDs.
        pipeline.tracker.track(blank_frame)
        vehicle_class_ids = pipeline.tracker.vehicle_class_ids

        # High confidence prevents a meaningless blank-frame candidate while
        # still initializing the licence-plate model's inference runtime.
        detect_plate_bboxes(blank_frame, confidence_threshold=0.99)

        # Initialize Paddle/PaddleX and oneDNN using the same public OCR path
        # that vehicle workers use later.
        run_ocr(blank_plate)

        elapsed = time.perf_counter() - started
        self.stdout.write(f"AI warm-up complete : {elapsed:.1f} seconds")
        return vehicle_class_ids

    def _handle_activity(self, track, result):
        with self._activity_lock:
            self._latest_activity = result
            self._latest_activity_until = time.monotonic() + 3.0

        if result.saved:
            action = (
                "DIAGNOSTIC would save"
                if self._diagnostic_only
                else f"Saved record #{result.record_id}"
            )
            self._write_success(
                f"{action} | Track: {track.track_id} "
                f"| Plate: {result.plate_text} | "
                f"Status: {result.authorization_status} | "
                f"{result.processing_ms:.0f}ms"
            )
            if not self._diagnostic_only and not result.authorized:
                try:
                    self.create_notification(result)
                except Exception as error:
                    self._write_error(
                        f"Notification creation failed: {error}"
                    )
        elif result.reason not in {"DUPLICATE_IGNORED"}:
            self._write_warning(
                f"Track {track.track_id}: {result.reason} "
                f"({result.candidates_attempted} candidate crops)"
            )

    def _handle_processing_error(self, track, error):
        self._write_error(
            f"Track {track.track_id} processing failed: {error}"
        )

    def create_notification(self, result):
        if not result.record_id:
            return

        record = EntryExitRecord.objects.select_related("gate").filter(
            pk=result.record_id
        ).first()
        if record is None:
            return

        if result.authorization_status == "EXPIRED":
            title = "Expired Vehicle Authorization"
            notification_type = Notification.Type.EXPIRED_AUTHORIZATION
        else:
            title = "Unauthorized Vehicle Detected"
            notification_type = Notification.Type.UNAUTHORIZED_VEHICLE

        gate_name = str(record.gate) if record.gate else "Unknown Gate"
        message = (
            f"Vehicle {result.plate_text} was detected at {gate_name}. "
            f"Status: {result.authorization_status}."
        )
        recipients = User.objects.filter(
            role__in=[User.Role.ADMIN, User.Role.SECURITY_GUARD],
            is_active=True,
        )
        Notification.objects.bulk_create(
            [
                Notification(
                    recipient=recipient,
                    notification_type=notification_type,
                    title=title,
                    message=message,
                    related_vehicle_id=record.vehicle_id,
                )
                for recipient in recipients
            ]
        )

    def draw_tracking_preview(self, *, frame, pipeline, frame_result, fps):
        line_start, line_end = pipeline.line_detector.line_pixels(
            frame.shape[1], frame.shape[0]
        )
        cv2.line(frame, line_start, line_end, (0, 255, 255), 2)

        with self._activity_lock:
            activity = (
                self._latest_activity
                if time.monotonic() <= self._latest_activity_until
                else None
            )

        detections = frame_result.detections if frame_result else ()
        for detection in detections:
            x1, y1, x2, y2 = detection.bbox
            color = (255, 170, 0) if detection.is_tracked else (160, 160, 160)
            status = ""
            if activity is not None and activity.track_id == detection.track_id:
                if activity.saved:
                    status = activity.authorization_status
                    color = (0, 200, 0) if activity.authorized else (0, 0, 255)
                else:
                    status = activity.reason

            track_label = (
                f"ID {detection.track_id}"
                if detection.track_id is not None
                else "UNTRACKED"
            )
            label = (
                f"{track_label} | {detection.vehicle_type} | "
                f"{detection.confidence * 100:.0f}%"
            )
            if status:
                label += f" | {status}"

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                label[:90],
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )

        worker_stats = pipeline.worker_pool.stats()
        overlay = (
            f"FPS {fps:.1f} | Vehicles {len(detections)} | "
            f"Queue {worker_stats.queue_size}/{worker_stats.queue_capacity} | "
            f"Workers {worker_stats.in_flight}/{worker_stats.live_workers}"
        )
        cv2.rectangle(frame, (8, 8), (min(frame.shape[1] - 8, 650), 42), (0, 0, 0), -1)
        cv2.putText(
            frame,
            overlay,
            (16, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
        )

        if activity is not None:
            activity_text = (
                f"Latest: {activity.plate_text or 'NO PLATE'} | "
                f"{activity.authorization_status} | {activity.reason}"
            )
            cv2.putText(
                frame,
                activity_text[:100],
                (16, 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )

    def _write_success(self, message):
        with self._output_lock:
            self.stdout.write(self.style.SUCCESS(message))

    def _write_warning(self, message):
        with self._output_lock:
            self.stdout.write(self.style.WARNING(message))

    def _write_error(self, message):
        with self._output_lock:
            self.stderr.write(self.style.ERROR(message))