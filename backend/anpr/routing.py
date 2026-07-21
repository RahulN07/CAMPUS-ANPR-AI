"""WebSocket routes for the ANPR application."""

from django.urls import path

from .consumers import GateLiveConsumer


websocket_urlpatterns = [
    path(
        "ws/anpr/gates/<int:gate_id>/",
        GateLiveConsumer.as_asgi(),
        name="anpr-gate-live",
    ),
]