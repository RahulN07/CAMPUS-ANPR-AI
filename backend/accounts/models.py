from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        SECURITY_GUARD = 'SECURITY_GUARD', 'Security Guard'
        FACULTY = 'FACULTY', 'Faculty'
        VIEWER = 'VIEWER', 'Viewer'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    phone = models.CharField(max_length=15, blank=True, null=True)
    department = models.ForeignKey(
        'access_management.Department',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
    )
    profile_image = models.ImageField(upload_to='profiles/', blank=True, null=True)
    is_active_staff = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_security_guard(self):
        return self.role == self.Role.SECURITY_GUARD


class AuditLog(models.Model):
    """Tracks sensitive actions across the system for accountability."""

    class Action(models.TextChoices):
        CREATE = 'CREATE', 'Create'
        UPDATE = 'UPDATE', 'Update'
        DELETE = 'DELETE', 'Delete'
        LOGIN = 'LOGIN', 'Login'
        LOGOUT = 'LOGOUT', 'Logout'
        ACCESS_GRANTED = 'ACCESS_GRANTED', 'Access Granted'
        ACCESS_DENIED = 'ACCESS_DENIED', 'Access Denied'

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=30, choices=Action.choices)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=50, blank=True, null=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} by {self.user} at {self.timestamp}"
