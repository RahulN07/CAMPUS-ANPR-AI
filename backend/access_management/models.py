from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Department(models.Model):
    class Code(models.TextChoices):
        CSE = 'CSE', 'Computer Science Engineering'
        CSD = 'CSD', 'Computer Science & Design'
        AI = 'AI', 'Artificial Intelligence'
        ECE = 'ECE', 'Electronics & Communication'
        EEE = 'EEE', 'Electrical & Electronics'
        MECHANICAL = 'MECHANICAL', 'Mechanical Engineering'
        CIVIL = 'CIVIL', 'Civil Engineering'
        ADMINISTRATION = 'ADMINISTRATION', 'Administration'
        LIBRARY = 'LIBRARY', 'Library'

    name = models.CharField(max_length=20, choices=Code.choices, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.get_name_display()


class Gate(models.Model):
    """Physical campus gate monitored by an ANPR camera."""

    class GateType(models.TextChoices):
        ENTRY = "ENTRY", "Entry Gate"
        EXIT = "EXIT", "Exit Gate"

    class CameraSource(models.TextChoices):
        WEBCAM = "WEBCAM", "Laptop/Web Camera"
        USB_CAMERA = "USB_CAMERA", "USB Camera"
        IP_CAMERA = "IP_CAMERA", "IP Camera"
        RTSP = "RTSP", "RTSP Stream"
        CCTV = "CCTV", "CCTV Camera"
        VIDEO_UPLOAD = "VIDEO_UPLOAD", "Uploaded Video"

    class CrossingDirection(models.TextChoices):
        ANY = "ANY", "Either Direction"
        A_TO_B = "A_TO_B", "Side A to Side B"
        B_TO_A = "B_TO_A", "Side B to Side A"

    name = models.CharField(
        max_length=100,
        unique=True,
    )

    gate_type = models.CharField(
        max_length=10,
        choices=GateType.choices,
        default=GateType.ENTRY,
    )

    location = models.CharField(
        max_length=150,
        blank=True,
    )

    camera_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Example: Camera 1, Camera 2",
    )

    camera_source = models.CharField(
        max_length=20,
        choices=CameraSource.choices,
        default=CameraSource.WEBCAM,
    )

    # Kept for compatibility with the current API and frontend.
    # It can contain an IP address, HTTP URL, or RTSP URL.
    camera_ip = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text=(
            "IP address, HTTP stream URL, or RTSP URL. "
            "Leave blank for local cameras."
        ),
    )

    camera_device_index = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(32)],
        help_text=(
            "OpenCV device index for laptop and USB cameras. "
            "The default camera is 0."
        ),
    )

    target_fps = models.PositiveSmallIntegerField(
        default=10,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(30),
        ],
        help_text="Target camera processing rate.",
    )

    line_crossing_enabled = models.BooleanField(
        default=True,
    )

    # Coordinates are normalized from 0.0 to 1.0 so the line works at
    # every camera resolution.
    line_start_x = models.FloatField(
        default=0.10,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    line_start_y = models.FloatField(
        default=0.50,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    line_end_x = models.FloatField(
        default=0.90,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    line_end_y = models.FloatField(
        default=0.50,
        validators=[
            MinValueValidator(0.0),
            MaxValueValidator(1.0),
        ],
    )

    crossing_direction = models.CharField(
        max_length=10,
        choices=CrossingDirection.choices,
        default=CrossingDirection.ANY,
        help_text=(
            "Allowed physical movement across the configured line. "
            "The gate type still controls whether the saved event is "
            "ENTRY or EXIT."
        ),
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()

        same_x = abs(
            self.line_start_x - self.line_end_x
        ) < 0.000001

        same_y = abs(
            self.line_start_y - self.line_end_y
        ) < 0.000001

        if self.line_crossing_enabled and same_x and same_y:
            raise ValidationError(
                {
                    "line_end_x": (
                        "The line start and end points "
                        "must be different."
                    ),
                    "line_end_y": (
                        "The line start and end points "
                        "must be different."
                    ),
                }
            )

    def __str__(self):
        return (
            f"{self.name} "
            f"({self.get_gate_type_display()})"
        )

class RolePermission(models.Model):
    """Fine-grained permission flags per role, editable from the admin UI."""
    role = models.CharField(max_length=20, unique=True)
    can_manage_vehicles = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)
    can_view_reports = models.BooleanField(default=True)
    can_export_data = models.BooleanField(default=False)
    can_manage_settings = models.BooleanField(default=False)

    def __str__(self):
        return f"Permissions for {self.role}"


class SystemSettings(models.Model):
    """
    Singleton row backing the Settings > General page.
    Camera/Email/Backup config largely lives in environment variables
    (see backend/.env) since those touch infrastructure, not just display
    preferences — this model only covers the user-editable display/behavior
    settings shown in the UI.
    """
    system_name = models.CharField(max_length=150, default='Campus Security System')
    organization = models.CharField(max_length=150, blank=True)
    timezone = models.CharField(max_length=50, default='Asia/Kolkata')
    date_format = models.CharField(max_length=20, default='DD MMM YYYY')
    time_format = models.CharField(max_length=10, default='12_HOUR', choices=[
        ('12_HOUR', '12 Hour'), ('24_HOUR', '24 Hour'),
    ])
    items_per_page = models.PositiveIntegerField(default=10)

    # ANPR behavior settings
    anpr_confidence_threshold = models.FloatField(default=0.4)
    auto_flag_unauthorized = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.system_name

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
