from rest_framework import serializers

from .models import Department, Gate


class DepartmentSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = "__all__"

    def get_display_name(self, obj):
        return str(obj)


class GateSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Gate
        fields = (
            "id",
            "display_name",
            "name",
            "gate_type",
            "location",
            "camera_name",
            "camera_source",
            "camera_ip",
            "camera_device_index",
            "target_fps",
            "line_crossing_enabled",
            "line_start_x",
            "line_start_y",
            "line_end_x",
            "line_end_y",
            "crossing_direction",
            "is_active",
            "created_at",
        )
        read_only_fields = (
            "id",
            "display_name",
            "created_at",
        )

    def get_display_name(self, obj):
        return str(obj)

    def validate_camera_ip(self, value):
        if value is None:
            return None

        cleaned_value = value.strip()

        return cleaned_value or None

    def validate(self, attrs):
        attrs = super().validate(attrs)

        instance = self.instance

        def current_value(field_name):
            if field_name in attrs:
                return attrs[field_name]

            if instance is not None:
                return getattr(instance, field_name)

            return (
                Gate._meta
                .get_field(field_name)
                .get_default()
            )

        camera_source = current_value("camera_source")
        camera_ip = current_value("camera_ip")

        stream_sources = {
            Gate.CameraSource.IP_CAMERA,
            Gate.CameraSource.RTSP,
        }

        camera_configuration_changed = (
            instance is None
            or "camera_source" in attrs
            or "camera_ip" in attrs
        )

        if (
            camera_configuration_changed
            and camera_source in stream_sources
            and not camera_ip
        ):
            raise serializers.ValidationError(
                {
                    "camera_ip": (
                        "An IP address or stream URL is "
                        "required for IP and RTSP cameras."
                    )
                }
            )

        line_crossing_enabled = current_value(
            "line_crossing_enabled"
        )

        line_start_x = current_value("line_start_x")
        line_start_y = current_value("line_start_y")
        line_end_x = current_value("line_end_x")
        line_end_y = current_value("line_end_y")

        same_x = abs(line_start_x - line_end_x) < 0.000001
        same_y = abs(line_start_y - line_end_y) < 0.000001

        if line_crossing_enabled and same_x and same_y:
            message = (
                "The line start and end points "
                "must be different."
            )

            raise serializers.ValidationError(
                {
                    "line_end_x": message,
                    "line_end_y": message,
                }
            )

        return attrs