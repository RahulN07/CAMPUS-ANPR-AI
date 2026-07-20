from django.contrib import admin

from .models import (
    Department,
    Gate,
    RolePermission,
    SystemSettings,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "created_at",
    )
    search_fields = ("name",)


@admin.register(Gate)
class GateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "gate_type",
        "location",
        "camera_source",
        "target_fps",
        "line_crossing_enabled",
        "is_active",
    )

    list_filter = (
        "gate_type",
        "camera_source",
        "line_crossing_enabled",
        "is_active",
    )

    search_fields = (
        "name",
        "location",
        "camera_name",
    )

    ordering = ("name",)

    readonly_fields = ("created_at",)

    fieldsets = (
        (
            "Gate",
            {
                "fields": (
                    "name",
                    "gate_type",
                    "location",
                    "is_active",
                )
            },
        ),
        (
            "Camera",
            {
                "fields": (
                    "camera_name",
                    "camera_source",
                    "camera_ip",
                    "camera_device_index",
                    "target_fps",
                )
            },
        ),
        (
            "Line Crossing",
            {
                "fields": (
                    "line_crossing_enabled",
                    "line_start_x",
                    "line_start_y",
                    "line_end_x",
                    "line_end_y",
                    "crossing_direction",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at",),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "can_manage_users",
        "can_manage_vehicles",
        "can_view_reports",
        "can_export_data",
        "can_manage_settings",
    )


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "system_name",
        "organization",
        "timezone",
        "updated_at",
    )