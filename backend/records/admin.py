from django.contrib import admin
from .models import EntryExitRecord


@admin.register(EntryExitRecord)
class EntryExitRecordAdmin(admin.ModelAdmin):
    list_display = (
        "detected_plate_text",
        "direction",
        "gate",
        "was_authorized",
        "confidence_score",
        "detection_source",
        "timestamp",
    )

    list_filter = (
        "direction",
        "was_authorized",
        "detection_source",
        "gate",
    )

    search_fields = (
        "detected_plate_text",
        "vehicle__registration_number",
        "vehicle__owner_name",
    )

    ordering = ("-timestamp",)