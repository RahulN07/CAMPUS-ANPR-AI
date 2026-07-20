from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from access_management.models import Department


class VehicleCompany(models.Model):
    class VehicleType(models.TextChoices):
        TWO_WHEELER = "TWO_WHEELER", "Two Wheeler"
        FOUR_WHEELER = "FOUR_WHEELER", "Four Wheeler"
        HEAVY_VEHICLE = "HEAVY_VEHICLE", "Heavy Vehicle"

    name = models.CharField(max_length=100)
    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["vehicle_type", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "vehicle_type"],
                name="unique_company_per_vehicle_type",
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.get_vehicle_type_display()}"


class VehicleModel(models.Model):
    company = models.ForeignKey(
        VehicleCompany,
        on_delete=models.CASCADE,
        related_name="models",
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="unique_model_per_company",
            )
        ]

    def __str__(self):
        return f"{self.company.name} {self.name}"


class Vehicle(models.Model):
    class OwnerType(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        FACULTY = "FACULTY", "Faculty"
        STAFF = "STAFF", "Staff"
        CLERK = "CLERK", "Clerk"
        VISITOR = "VISITOR", "Visitor"

    class VehicleType(models.TextChoices):
        TWO_WHEELER = "TWO_WHEELER", "Two Wheeler"
        FOUR_WHEELER = "FOUR_WHEELER", "Four Wheeler"
        HEAVY_VEHICLE = "HEAVY_VEHICLE", "Heavy Vehicle"

    class FuelType(models.TextChoices):
        PETROL = "PETROL", "Petrol"
        DIESEL = "DIESEL", "Diesel"
        ELECTRIC = "ELECTRIC", "Electric"
        CNG = "CNG", "CNG"
        HYBRID = "HYBRID", "Hybrid"

    class AuthorizationStatus(models.TextChoices):
        AUTHORIZED = "AUTHORIZED", "Authorized"
        UNAUTHORIZED = "UNAUTHORIZED", "Unauthorized"
        EXPIRED = "EXPIRED", "Expired"
        PENDING = "PENDING", "Pending Review"

    plate_regex = RegexValidator(
        regex=r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$",
        message="Registration number must be in the format KA25AB1234.",
    )

    # Owner information
    owner_name = models.CharField(max_length=150)
    owner_email = models.EmailField(blank=True, null=True)
    owner_phone = models.CharField(max_length=15, blank=True, null=True)
    owner_type = models.CharField(
        max_length=20,
        choices=OwnerType.choices,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )

    # Vehicle information
    vehicle_company = models.CharField(max_length=100)
    vehicle_model = models.CharField(max_length=100)

    vehicle_type = models.CharField(
        max_length=20,
        choices=VehicleType.choices,
        default=VehicleType.TWO_WHEELER,
    )

    color = models.CharField(max_length=50)

    fuel_type = models.CharField(
        max_length=20,
        choices=FuelType.choices,
    )

    # Registration information
    registration_number = models.CharField(
        max_length=15,
        unique=True,
        validators=[plate_regex],
        db_index=True,
    )

    registration_date = models.DateField()
    valid_from = models.DateField()
    valid_until = models.DateField()

    vehicle_image = models.ImageField(
        upload_to="vehicles/",
        blank=True,
        null=True,
    )

    authorization_status = models.CharField(
        max_length=20,
        choices=AuthorizationStatus.choices,
        default=AuthorizationStatus.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["registration_number"]),
            models.Index(fields=["authorization_status"]),
        ]

    def __str__(self):
        return f"{self.registration_number} - {self.owner_name}"

    @property
    def is_expired(self):
        return self.valid_until < timezone.now().date()

    def clean(self):
        super().clean()

        if self.valid_from and self.valid_until:
            if self.valid_until < self.valid_from:
                from django.core.exceptions import ValidationError

                raise ValidationError(
                    {
                        "valid_until": (
                            "Valid until date cannot be earlier than valid from date."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.registration_number = (
            self.registration_number.upper()
            .replace(" ", "")
            .replace("-", "")
        )

        self.vehicle_company = self.vehicle_company.strip()
        self.vehicle_model = self.vehicle_model.strip()
        self.color = self.color.strip()

        super().save(*args, **kwargs)