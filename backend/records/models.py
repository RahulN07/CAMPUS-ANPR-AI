from django.db import models

from access_management.models import Gate
from accounts.models import User
from vehicles.models import Vehicle


class EntryExitRecord(models.Model):
    class Direction(models.TextChoices):
        ENTRY = "ENTRY", "Entry"
        EXIT = "EXIT", "Exit"

    class DetectionSource(models.TextChoices):
        WEBCAM = "WEBCAM", "Live Webcam"
        UPLOAD = "UPLOAD", "Image Upload"
        CCTV = "CCTV", "CCTV Camera"
        MANUAL = "MANUAL", "Manual Entry"

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="records",
    )

    detected_plate_text = models.CharField(
        max_length=20,
        db_index=True,
    )

    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
    )

    gate = models.ForeignKey(
        Gate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="records",
    )

    was_authorized = models.BooleanField(default=False)

    confidence_score = models.FloatField(default=0.0)

    detection_source = models.CharField(
        max_length=10,
        choices=DetectionSource.choices,
        default=DetectionSource.WEBCAM,
    )

    # ------------------------------------------------------------
    # Detected vehicle attributes (audit trail).
    #
    # These are always what the CV pipeline detected on THIS frame,
    # even for a registered/authorized vehicle -- the registered
    # Vehicle row (vehicle.vehicle_type / vehicle.color / ...) stays
    # the trusted source of truth and is never overwritten from here.
    # ------------------------------------------------------------

    detected_vehicle_type = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    vehicle_type_confidence = models.FloatField(default=0.0)

    vehicle_color = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    vehicle_color_confidence = models.FloatField(default=0.0)

    detected_vehicle_company = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    detected_vehicle_model = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    vehicle_make_model_confidence = models.FloatField(default=0.0)

    # Complete CCTV frame containing the vehicle
    captured_image = models.ImageField(
        upload_to="records/full_frames/",
        blank=True,
        null=True,
    )

    # Cropped number plate image
    plate_image = models.ImageField(
        upload_to="records/plate_crops/",
        blank=True,
        null=True,
    )

    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_entries",
    )

    timestamp = models.DateTimeField(auto_now_add=True)

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp"]),
            models.Index(fields=["direction"]),
            models.Index(fields=["detected_plate_text"]),
            models.Index(fields=["was_authorized"]),
        ]

    def __str__(self):
        return (
            f"{self.detected_plate_text} - "
            f"{self.direction} @ {self.timestamp}"
        )