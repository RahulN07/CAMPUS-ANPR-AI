import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from access_management.models import Gate
from accounts.models import User
from anpr.camera_capture import CameraCaptureService
from anpr.detector import run_full_pipeline
from notifications.models import Notification
from records.models import EntryExitRecord


class Command(BaseCommand):
    help = (
        "Run non-blocking CCTV ANPR using a webcam, video file, "
        "HTTP stream, or RTSP stream."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=False,
            help=(
                "Optional source override. Use a camera index, video "
                "path, HTTP URL, or RTSP URL. When omitted, the Gate "
                "camera configuration is used."
            ),
        )

        parser.add_argument(
            "--gate",
            type=int,
            required=True,
        )

        parser.add_argument(
            "--direction",
            choices=["ENTRY", "EXIT"],
            default=None,
            help=(
                "Deprecated compatibility option. The value must match "
                "the selected Gate's configured gate_type."
            ),
        )

        parser.add_argument(
            "--recorded-by",
            type=int,
            required=True,
        )

        parser.add_argument(
            "--detection-interval",
            type=float,
            default=0.35,
            help=(
                "Minimum seconds between submitted detections. "
                "Lower values check more frequently."
            ),
        )

        parser.add_argument(
            "--cooldown",
            type=int,
            default=5,
        )

        parser.add_argument(
            "--confidence",
            type=float,
            default=0.45,
        )

        parser.add_argument(
            "--required-votes",
            type=int,
            default=2,
            help="Stable readings required before saving.",
        )

        parser.add_argument(
            "--vote-timeout",
            type=float,
            default=5,
        )

        parser.add_argument(
            "--show",
            action="store_true",
        )

    def handle(self, *args, **options):
        gate_id = options["gate"]
        user_id = options["recorded_by"]

        detection_interval = max(
            0.1,
            float(options["detection_interval"]),
        )

        cooldown_seconds = max(
            1,
            int(options["cooldown"]),
        )

        required_votes = max(
            1,
            int(options["required_votes"]),
        )

        vote_timeout = max(
            1,
            float(options["vote_timeout"]),
        )

        confidence_threshold = max(
            0.0,
            min(1.0, float(options["confidence"])),
        )

        show_preview = options["show"]

        gate = Gate.objects.filter(id=gate_id).first()

        if gate is None:
            raise CommandError(
                f"Gate ID {gate_id} does not exist."
            )

        if not gate.is_active:
            raise CommandError(
                f"Gate ID {gate_id} is inactive."
            )

        # Gate configuration is authoritative. Entry gates always save ENTRY
        # and exit gates always save EXIT. The deprecated CLI option is only
        # accepted when it agrees with the configured gate type.
        direction = gate.gate_type
        requested_direction = options.get("direction")

        if (
            requested_direction is not None
            and requested_direction != direction
        ):
            raise CommandError(
                "--direction conflicts with the selected Gate. "
                f"Gate {gate.id} is configured as {direction}."
            )

        recorded_by = User.objects.filter(
            id=user_id,
            is_active=True,
        ).first()

        if recorded_by is None:
            raise CommandError(
                f"Active user ID {user_id} does not exist."
            )

        source_argument = self.resolve_source_argument(
            gate=gate,
            source_override=options.get("source"),
        )

        source, is_video_file = self.parse_source(source_argument)

        capture_service = CameraCaptureService(
            source=source,
            gate_id=gate.id,
            target_fps=gate.target_fps,
            queue_size=30,
            source_name=gate.camera_name or gate.name,
            replay_video_in_real_time=is_video_file,
            reconnect_delay=2.0,
        ).start()

        self.stdout.write(
            self.style.SUCCESS(
                "Non-blocking CCTV ANPR started successfully."
            )
        )

        self.stdout.write(
            "Source             : "
            f"{self.describe_source(source_argument)}"
        )
        self.stdout.write(f"Gate               : {gate}")
        self.stdout.write(f"Direction          : {direction}")
        self.stdout.write(f"Capture FPS         : {gate.target_fps}")
        self.stdout.write("Frame queue         : 30 (drop oldest)")
        self.stdout.write(
            f"Detection interval : {detection_interval} seconds"
        )
        self.stdout.write(
            f"Required votes     : {required_votes}"
        )
        self.stdout.write(
            f"Cooldown           : {cooldown_seconds} seconds"
        )

        if show_preview:
            self.stdout.write(
                "Press Q inside the video window to stop."
            )

        executor = ThreadPoolExecutor(max_workers=1)
        detection_future = None

        last_detection_submission = 0.0
        last_processed_sequence = -1

        recently_saved = {}
        plate_votes = {}

        latest_display_result = None
        latest_display_message = ""
        display_result_until = 0.0
        latest_frame = None
        latest_frame_sequence = -1

        try:
            while True:
                packet = None

                try:
                    packet = capture_service.get_frame(
                        timeout=0.05,
                    )
                except queue.Empty:
                    packet = None

                if packet is not None:
                    try:
                        latest_frame = packet.frame
                        latest_frame_sequence = packet.sequence
                    finally:
                        capture_service.task_done()

                current_time = time.time()

                # Process completed AI work without blocking preview.
                if (
                    detection_future is not None
                    and detection_future.done()
                ):
                    try:
                        processed_frame, result = (
                            detection_future.result()
                        )

                        (
                            latest_display_message,
                            saved_result,
                        ) = self.handle_detection_result(
                            frame=processed_frame,
                            result=result,
                            plate_votes=plate_votes,
                            recently_saved=recently_saved,
                            required_votes=required_votes,
                            vote_timeout=vote_timeout,
                            cooldown_seconds=cooldown_seconds,
                            gate=gate,
                            direction=direction,
                            recorded_by=recorded_by,
                        )

                        latest_display_result = result
                        display_result_until = time.time() + 2.0

                        if saved_result:
                            latest_display_message = "SAVED"

                    except Exception as error:
                        self.stderr.write(
                            self.style.ERROR(
                                f"Detection processing failed: {error}"
                            )
                        )

                    finally:
                        detection_future = None

                # Submit only one detection job at a time.
                # The preview remains smooth while this job runs.
                can_submit_detection = (
                    latest_frame is not None
                    and detection_future is None
                    and latest_frame_sequence
                    != last_processed_sequence
                    and (
                        current_time - last_detection_submission
                        >= detection_interval
                    )
                )

                if can_submit_detection:
                    frame_for_detection = latest_frame.copy()

                    detection_future = executor.submit(
                        self.process_frame,
                        frame_for_detection,
                        confidence_threshold,
                    )

                    last_detection_submission = current_time
                    last_processed_sequence = (
                        latest_frame_sequence
                    )

                if show_preview and latest_frame is not None:
                    preview_frame = latest_frame.copy()

                    if (
                        latest_display_result is not None
                        and time.time() <= display_result_until
                    ):
                        self.draw_detection(
                            preview_frame,
                            latest_display_result,
                            latest_display_message,
                        )

                    cv2.imshow(
                        "Campus CCTV ANPR",
                        preview_frame,
                    )

                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

                capture_stats = capture_service.stats()

                if (
                    capture_stats.ended
                    and capture_stats.queue.size == 0
                    and detection_future is None
                ):
                    break

                if packet is None:
                    time.sleep(0.005)

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "CCTV ANPR stopped by user."
                )
            )

        finally:
            capture_service.stop(
                timeout=5,
                clear_queue=True,
            )
            executor.shutdown(wait=False, cancel_futures=True)
            cv2.destroyAllWindows()

            capture_stats = capture_service.stats()

            self.stdout.write(
                "Capture summary     : "
                f"read={capture_stats.frames_read}, "
                f"queued={capture_stats.frames_enqueued}, "
                f"dropped={capture_stats.queue.dropped}, "
                f"reconnects={capture_stats.reconnects}"
            )

            self.stdout.write(
                self.style.SUCCESS(
                    "CCTV ANPR process closed."
                )
            )

    def resolve_source_argument(
        self,
        gate,
        source_override=None,
    ):
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
                    "The selected Gate does not have a camera "
                    "stream address configured."
                )

            return gate.camera_ip.strip()

        if gate.camera_source == Gate.CameraSource.VIDEO_UPLOAD:
            raise CommandError(
                "Uploaded-video gates require --source with a "
                "local video file path."
            )

        raise CommandError(
            f"Unsupported camera source: {gate.camera_source}"
        )

    def describe_source(self, source_argument):
        """Return a display-safe source label without URL credentials."""

        source_text = str(source_argument).strip()

        if source_text.isdigit():
            return f"local camera index {source_text}"

        if source_text.startswith("rtsp://"):
            return "RTSP stream"

        if source_text.startswith(
            ("http://", "https://")
        ):
            return "HTTP camera stream"

        return Path(source_text).name or "video file"

    def process_frame(
        self,
        frame,
        confidence_threshold,
    ):
        """
        Runs in a background thread.
        """

        close_old_connections()

        try:
            encoded_success, encoded_frame = cv2.imencode(
                ".jpg",
                frame,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    90,
                ],
            )

            if not encoded_success:
                return frame, None

            result = run_full_pipeline(
                encoded_frame.tobytes(),
                yolo_confidence=confidence_threshold,
            )

            return frame, result

        finally:
            close_old_connections()

    def handle_detection_result(
        self,
        frame,
        result,
        plate_votes,
        recently_saved,
        required_votes,
        vote_timeout,
        cooldown_seconds,
        gate,
        direction,
        recorded_by,
    ):
        if result is None:
            return "ENCODING FAILED", False

        if not result.success:
            error_message = result.error or "NO VALID PLATE"

            self.stdout.write(
                self.style.WARNING(
                    f"Detection skipped : {error_message}"
                )
            )

            if result.raw_plate_text:
                self.stdout.write(
                    f"Raw OCR text: {result.raw_plate_text}"
                )
            
            return error_message, False

        plate = result.cleaned_plate_text

        if not plate:
            return "EMPTY PLATE", False

        current_time = time.time()

        vote = plate_votes.get(plate)

        if (
            vote is None
            or current_time - vote["last_seen"] > vote_timeout
        ):
            plate_votes[plate] = {
                "count": 1,
                "last_seen": current_time,
                "best_result": result,
                "best_frame": frame.copy(),
            }
        else:
            vote["count"] += 1
            vote["last_seen"] = current_time

            previous_confidence = (
                vote["best_result"].confidence_score
            )

            if result.confidence_score > previous_confidence:
                vote["best_result"] = result
                vote["best_frame"] = frame.copy()

        vote = plate_votes[plate]
        vote_count = vote["count"]

        if vote_count < required_votes:
            self.stdout.write(
                f"Verifying plate: {plate} "
                f"({vote_count}/{required_votes})"
            )

            return (
                f"VERIFYING {vote_count}/{required_votes}",
                False,
            )

        previous_saved_time = recently_saved.get(plate)

        if (
            previous_saved_time is not None
            and current_time - previous_saved_time
            < cooldown_seconds
        ):
            return "COOLDOWN", False

        best_result = vote["best_result"]
        best_frame = vote["best_frame"]

        record = self.save_detection(
            frame=best_frame,
            result=best_result,
            gate=gate,
            direction=direction,
            recorded_by=recorded_by,
        )

        recently_saved[plate] = current_time
        plate_votes.pop(plate, None)

        self.stdout.write(
            self.style.SUCCESS(
                f"Saved record #{record.id} | "
                f"Plate: {best_result.cleaned_plate_text} | "
                f"Status: {best_result.authorization_status}"
            )
        )

        return "SAVED", True

    def parse_source(self, source_argument):
        source_text = str(source_argument).strip()

        if source_text.isdigit():
            return int(source_text), False

        if source_text.startswith(
            (
                "rtsp://",
                "http://",
                "https://",
            )
        ):
            return source_text, False

        source_path = Path(source_text)

        if source_path.exists():
            return str(source_path), True

        raise CommandError(
            f"Invalid video source: {source_text}"
        )

    def save_detection(
        self,
        frame,
        result,
        gate,
        direction,
        recorded_by,
    ):
        authorized = (
            result.authorization_status == "AUTHORIZED"
        )

        matched_vehicle = result.matched_vehicle or {}

        record = EntryExitRecord.objects.create(
            vehicle_id=matched_vehicle.get("id"),
            detected_plate_text=result.cleaned_plate_text,
            direction=direction,
            gate=gate,
            was_authorized=authorized,
            confidence_score=result.confidence_score,
            detection_source=(
                EntryExitRecord.DetectionSource.CCTV
            ),
            recorded_by=recorded_by,
            notes=self.build_notes(result),
        )

        frame_success, encoded_frame = cv2.imencode(
            ".jpg",
            frame,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                92,
            ],
        )

        if frame_success:
            record.captured_image.save(
                (
                    f"{result.cleaned_plate_text}_"
                    f"{record.id}_vehicle.jpg"
                ),
                ContentFile(encoded_frame.tobytes()),
                save=False,
            )

        if result.plate_crop_bytes:
            record.plate_image.save(
                (
                    f"{result.cleaned_plate_text}_"
                    f"{record.id}_plate.jpg"
                ),
                ContentFile(result.plate_crop_bytes),
                save=False,
            )

        record.save()

        if not authorized:
            self.create_notification(
                result=result,
                record=record,
            )

        return record

    def build_notes(self, result):
        vehicle = result.matched_vehicle or {}

        if result.authorization_status == "AUTHORIZED":
            return (
                "Authorized CCTV detection. "
                f"Owner: {vehicle.get('owner_name', 'Unknown')}. "
                f"Department: "
                f"{vehicle.get('department', 'Unknown')}."
            )

        if result.authorization_status == "EXPIRED":
            return (
                "Vehicle found, but authorization has expired."
            )

        return "Unauthorized vehicle detected by CCTV."

    def create_notification(self, result, record):
        if result.authorization_status == "EXPIRED":
            title = "Expired Vehicle Authorization"

            notification_type = (
                Notification.Type.EXPIRED_AUTHORIZATION
            )
        else:
            title = "Unauthorized Vehicle Detected"

            notification_type = (
                Notification.Type.UNAUTHORIZED_VEHICLE
            )

        gate_name = (
            str(record.gate)
            if record.gate
            else "Unknown Gate"
        )

        message = (
            f"Vehicle {result.cleaned_plate_text} was detected "
            f"at {gate_name}. "
            f"Status: {result.authorization_status}."
        )

        recipients = User.objects.filter(
            role__in=[
                User.Role.ADMIN,
                User.Role.SECURITY_GUARD,
            ],
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

    def draw_detection(
        self,
        frame,
        result,
        message="",
    ):
        if result is None:
            return

        x1, y1, x2, y2 = (
            result.bounding_box or [0, 0, 0, 0]
        )

        authorized = (
            result.authorization_status == "AUTHORIZED"
        )

        color = (
            (0, 200, 0)
            if authorized
            else (0, 0, 255)
        )

        if result.success:
            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                color,
                3,
            )

            confidence = (
                result.confidence_score * 100
                if result.confidence_score <= 1
                else result.confidence_score
            )

            label = (
                f"{result.cleaned_plate_text} | "
                f"{result.authorization_status} | "
                f"{confidence:.0f}%"
            )
        else:
            label = result.error or "NO VALID PLATE"
            color = (0, 165, 255)

        if message:
            label += f" | {message}"

        cv2.putText(
            frame,
            label[:110],
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )