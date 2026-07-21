"""ASGI entry point for HTTP and authenticated live ANPR WebSockets."""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django before importing routing modules that may access models.
django_asgi_application = get_asgi_application()

from anpr.routing import websocket_urlpatterns  # noqa: E402
from config.websocket_auth import JwtSubprotocolAuthMiddleware  # noqa: E402


application = ProtocolTypeRouter(
    {
        "http": django_asgi_application,
        "websocket": AllowedHostsOriginValidator(
            JwtSubprotocolAuthMiddleware(
                URLRouter(websocket_urlpatterns)
            )
        ),
    }
)