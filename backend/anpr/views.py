from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .detector import run_full_pipeline
from .serializers import DetectionRequestSerializer


DUPLICATE_WINDOW_SECONDS = 5


class DetectPlateView(APIView):
    """
    Handles ANPR image uploads and webcam captures.

    Current compatibility pipeline:
        image
        -> YOLOv8 licence-plate detection
        -> OpenCV preprocessing
        -> EasyOCR
        -> vehicle matching
        -> duplicate filtering
        -> record creation

    Continuous tracking and queue processing will be introduced in later
    phases without removing this upload endpoint.
    """

    permission_classes = [
        permissions.IsAuthenticated,
    ]

    def post(self, request):
        serializer = DetectionRequestSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        data = serializer.validated_data

        image_file = data["image"]
        image_bytes = image_file.read()

        result = run_full_pipeline(
            image_bytes
        )

        if not result.success:
            return Response(
                self._failed_response(result),
                status=status.HTTP_200_OK,
            )

        from access_management.models import Gate
        from records.models import EntryExitRecord

        gate = self._get_gate(
            Gate,
            data.get("gate"),
        )

        matched_vehicle = (
            result.matched_vehicle or {}
        )

        vehicle_id = matched_vehicle.get("id")

        was_authorized = (
            result.authorization_status
            == "AUTHORIZED"
        )

        with transaction.atomic():
            duplicate_record = (
                self._find_recent_duplicate(
                    EntryExitRecord=EntryExitRecord,
                    plate=result.cleaned_plate_text,
                    direction=data["direction"],
                    gate=gate,
                )
            )

            if duplicate_record is not None:
                return Response(
                    self._successful_response(
                        request=request,
                        result=result,
                        record=duplicate_record,
                        matched_vehicle=matched_vehicle,
                        entry_recorded=False,
                        duplicate_ignored=True,
                    ),
                    status=status.HTTP_200_OK,
                )

            record = EntryExitRecord.objects.create(
                vehicle_id=vehicle_id,
                detected_plate_text=(
                    result.cleaned_plate_text
                ),
                direction=data["direction"],
                gate=gate,
                was_authorized=was_authorized,
                confidence_score=(
                    result.confidence_score
                ),
                detection_source=data["source"],
                recorded_by=request.user,
            )

            self._save_detection_images(
                record=record,
                image_file=image_file,
                plate_crop_bytes=(
                    result.plate_crop_bytes
                ),
                plate=(
                    result.cleaned_plate_text
                    or "unknown"
                ),
            )

        if not was_authorized:
            self._notify_admins(
                result=result,
                record=record,
            )

        return Response(
            self._successful_response(
                request=request,
                result=result,
                record=record,
                matched_vehicle=matched_vehicle,
                entry_recorded=True,
                duplicate_ignored=False,
            ),
            status=status.HTTP_201_CREATED,
        )

    def _get_gate(
        self,
        Gate,
        gate_id,
    ):
        if not gate_id:
            return None

        return (
            Gate.objects
            .filter(
                pk=gate_id,
                is_active=True,
            )
            .first()
        )

    def _find_recent_duplicate(
        self,
        EntryExitRecord,
        plate,
        direction,
        gate,
    ):
        """
        Find the same plate recorded at the same gate and in the same
        direction during the duplicate window.

        The later tracking architecture will also use camera and track IDs.
        """

        cutoff = (
            timezone.now()
            - timedelta(
                seconds=DUPLICATE_WINDOW_SECONDS
            )
        )

        queryset = (
            EntryExitRecord.objects
            .filter(
                detected_plate_text=plate,
                direction=direction,
                timestamp__gte=cutoff,
            )
            .order_by("-timestamp")
        )

        if gate is None:
            queryset = queryset.filter(
                gate__isnull=True
            )
        else:
            queryset = queryset.filter(
                gate=gate
            )

        return queryset.first()

    def _save_detection_images(
        self,
        record,
        image_file,
        plate_crop_bytes,
        plate,
    ):
        """
        Save the full frame and plate crop without performing multiple
        unnecessary model saves.
        """

        changed_fields = []

        image_file.seek(0)

        record.captured_image.save(
            f"{plate}_{record.id}_frame.jpg",
            ContentFile(
                image_file.read()
            ),
            save=False,
        )

        changed_fields.append(
            "captured_image"
        )

        if plate_crop_bytes:
            record.plate_image.save(
                f"{plate}_{record.id}_plate.jpg",
                ContentFile(
                    plate_crop_bytes
                ),
                save=False,
            )

            changed_fields.append(
                "plate_image"
            )

        if changed_fields:
            record.save(
                update_fields=changed_fields
            )

    def _failed_response(
        self,
        result,
    ):
        return {
            "success": False,
            "plate": (
                result.raw_plate_text or ""
            ),
            "confidence_score": (
                result.confidence_score or 0
            ),
            "bounding_box": (
                result.bounding_box
            ),
            "plate_candidates": (
                result.plate_candidates
            ),
            "owner": None,
            "department": None,
            "vehicle_type": None,
            "owner_type": None,
            "vehicle_company": None,
            "vehicle_model": None,
            "color": None,
            "authorization_status": "UNKNOWN",
            "record_id": None,
            "entry_recorded": False,
            "duplicate_ignored": False,
            "captured_image": None,
            "plate_image": None,
            "error": (
                result.error
                or "Detection failed."
            ),
        }

    def _successful_response(
        self,
        request,
        result,
        record,
        matched_vehicle,
        entry_recorded,
        duplicate_ignored,
    ):
        captured_image = None
        plate_image = None

        if record.captured_image:
            captured_image = (
                request.build_absolute_uri(
                    record.captured_image.url
                )
            )

        if record.plate_image:
            plate_image = (
                request.build_absolute_uri(
                    record.plate_image.url
                )
            )

        return {
            "success": True,
            "plate": (
                result.cleaned_plate_text
            ),
            "confidence_score": (
                result.confidence_score
            ),
            "bounding_box": (
                result.bounding_box
            ),
            "plate_candidates": (
                result.plate_candidates
            ),
            "owner": matched_vehicle.get(
                "owner_name"
            ),
            "department": matched_vehicle.get(
                "department"
            ),
            "vehicle_type": matched_vehicle.get(
                "vehicle_type"
            ),
            "owner_type": matched_vehicle.get(
                "owner_type"
            ),
            "vehicle_company": matched_vehicle.get(
                "vehicle_company"
            ),
            "vehicle_model": matched_vehicle.get(
                "vehicle_model"
            ),
            "color": matched_vehicle.get(
                "color"
            ),
            "authorization_status": (
                result.authorization_status
            ),
            "record_id": record.id,
            "entry_recorded": entry_recorded,
            "duplicate_ignored": (
                duplicate_ignored
            ),
            "captured_image": captured_image,
            "plate_image": plate_image,
            "error": None,
        }

    def _notify_admins(
        self,
        result,
        record,
    ):
        from accounts.models import User
        from notifications.models import Notification

        if (
            result.authorization_status
            == "UNAUTHORIZED"
        ):
            title = (
                "Unauthorized Vehicle Detected"
            )

            notification_type = (
                Notification.Type
                .UNAUTHORIZED_VEHICLE
            )
        else:
            title = (
                "Expired Vehicle Authorization"
            )

            notification_type = (
                Notification.Type
                .EXPIRED_AUTHORIZATION
            )

        plate = (
            result.cleaned_plate_text
            or result.raw_plate_text
            or "UNREADABLE"
        )

        gate_name = (
            str(record.gate)
            if record.gate
            else "Unknown Gate"
        )

        message = (
            f"Plate {plate} was detected at "
            f"{gate_name} and was flagged as "
            f"{result.authorization_status}."
        )

        recipients = (
            User.objects
            .filter(
                role__in=[
                    User.Role.ADMIN,
                    User.Role.SECURITY_GUARD,
                ],
                is_active=True,
            )
            .only("id")
        )

        notifications = [
            Notification(
                recipient=user,
                notification_type=(
                    notification_type
                ),
                title=title,
                message=message,
                related_vehicle_id=(
                    record.vehicle_id
                ),
            )
            for user in recipients
        ]

        if notifications:
            Notification.objects.bulk_create(
                notifications
            )


class RecentDetectionsView(APIView):
    """
    Return recent ANPR records for the existing Live Monitor page.
    """

    permission_classes = [
        permissions.IsAuthenticated,
    ]

    def get(self, request):
        from records.models import EntryExitRecord
        from records.serializers import EntryExitRecordSerializer

        try:
            limit = int(
                request.query_params.get(
                    "limit",
                    10,
                )
            )
        except (TypeError, ValueError):
            limit = 10

        limit = max(
            1,
            min(limit, 100),
        )

        records = (
            EntryExitRecord.objects
            .select_related(
                "vehicle",
                "vehicle__department",
                "gate",
                "recorded_by",
            )
            .order_by("-timestamp")[:limit]
        )

        serializer = EntryExitRecordSerializer(
            records,
            many=True,
            context={
                "request": request,
            },
        )

        return Response(
            serializer.data
        )