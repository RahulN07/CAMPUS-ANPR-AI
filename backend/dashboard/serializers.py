from rest_framework import serializers


class DashboardSerializer(serializers.Serializer):
    total_vehicles = serializers.IntegerField()
    authorized_vehicles = serializers.IntegerField()
    unauthorized_vehicles = serializers.IntegerField()
    today_entries = serializers.IntegerField()
    today_exits = serializers.IntegerField()
    recent_records = serializers.ListField()