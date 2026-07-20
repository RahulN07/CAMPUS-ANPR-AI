from django.db import models
from accounts.models import User
from vehicles.models import Vehicle


class Notification(models.Model):
    class Type(models.TextChoices):
        UNAUTHORIZED_VEHICLE = 'UNAUTHORIZED_VEHICLE', 'Unauthorized Vehicle'
        AUTHORIZED_ENTRY = 'AUTHORIZED_ENTRY', 'Authorized Entry'
        EXPIRED_VEHICLE = 'EXPIRED_VEHICLE', 'Expired Vehicle'
        EXPIRED_AUTHORIZATION = 'EXPIRED_AUTHORIZATION', 'Expired Authorization'
        SYSTEM_ALERT = 'SYSTEM_ALERT', 'System Alert'

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=30, choices=Type.choices)
    title = models.CharField(max_length=200)
    message = models.TextField()
    related_vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} -> {self.recipient}"
