from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings
from django.urls import path

from anpr.consumers import (
    WS_CLOSE_GATE_NOT_FOUND,
    WS_CLOSE_UNAUTHORIZED,
    GateLiveConsumer,
)


class FakeLiveTransport:
    def __init__(self):
        self.status_calls = []
        self.event_calls = []

    def get_status(self, gate_id):
        self.status_calls.append(gate_id)
        return {
            "gate_id": gate_id,
            "state": "RUNNING",
            "fps": 9.8,
            "frame_queue_size": 0,
        }

    def recent_events(self, gate_id, limit):
        self.event_calls.append((gate_id, limit))
        return [
            {
                "record_id": 101,
                "plate": "KA02MM9091",
                "authorized": False,
            }
        ]


class InjectUserMiddleware:
    def __init__(self, application, user):
        self.application = application
        self.user = user

    async def __call__(self, scope, receive, send):
        scoped = dict(scope)
        scoped["user"] = self.user
        await self.application(scoped, receive, send)


websocket_routes = URLRouter(
    [
        path(
            "ws/anpr/gates/<int:gate_id>/",
            GateLiveConsumer.as_asgi(),
        )
    ]
)


IN_MEMORY_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class GateLiveConsumerTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            username="viewer",
        )
        self.transport = FakeLiveTransport()

    def application(self, user=None):
        return InjectUserMiddleware(
            websocket_routes,
            self.user if user is None else user,
        )

    def communicator(self, user=None, subprotocols=None):
        return WebsocketCommunicator(
            self.application(user),
            "/ws/anpr/gates/1/",
            subprotocols=(
                ["anpr.v1", "aaa.bbb.ccc"]
                if subprotocols is None
                else subprotocols
            ),
        )

    async def connect(self, user=None, gate_exists=True):
        communicator = self.communicator(user)

        with (
            patch(
                "anpr.consumers.active_gate_exists",
                new=AsyncMock(return_value=gate_exists),
            ),
            patch(
                "anpr.consumers.get_live_transport",
                return_value=self.transport,
            ),
        ):
            connected, subprotocol = await communicator.connect()

        return communicator, connected, subprotocol

    async def test_authenticated_user_receives_initial_snapshot(self):
        communicator, connected, subprotocol = await self.connect()
        self.assertTrue(connected)
        self.assertEqual(subprotocol, "anpr.v1")

        message = await communicator.receive_json_from(timeout=1)
        self.assertEqual(message["type"], "snapshot")
        self.assertEqual(message["data"]["gate_id"], 1)
        self.assertEqual(message["data"]["status"]["state"], "RUNNING")
        self.assertEqual(
            message["data"]["recent_events"][0]["plate"],
            "KA02MM9091",
        )
        self.assertEqual(self.transport.status_calls, [1])
        self.assertEqual(self.transport.event_calls, [(1, 25)])

        await communicator.disconnect()

    async def test_anonymous_user_is_rejected(self):
        communicator, connected, close_code = await self.connect(
            user=AnonymousUser()
        )

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_inactive_user_is_rejected(self):
        inactive_user = SimpleNamespace(
            is_authenticated=True,
            is_active=False,
        )
        communicator, connected, close_code = await self.connect(
            user=inactive_user
        )

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_inactive_or_missing_gate_is_rejected(self):
        communicator, connected, close_code = await self.connect(
            gate_exists=False
        )

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_GATE_NOT_FOUND)

    async def test_ping_returns_pong(self):
        communicator, connected, _ = await self.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=1)

        await communicator.send_json_to({"type": "ping"})
        response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "pong")
        self.assertIn("server_time", response["data"])
        await communicator.disconnect()

    async def test_get_status_returns_fresh_snapshot(self):
        communicator, connected, _ = await self.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=1)

        await communicator.send_json_to({"type": "get_status"})
        response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "snapshot")
        self.assertEqual(self.transport.status_calls, [1, 1])
        self.assertEqual(self.transport.event_calls, [(1, 25), (1, 25)])
        await communicator.disconnect()

    async def test_unsupported_client_message_returns_error(self):
        communicator, connected, _ = await self.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=1)

        await communicator.send_json_to({"type": "publish_detection"})
        response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "error")
        self.assertEqual(
            response["error"]["code"],
            "unsupported_message",
        )
        await communicator.disconnect()

    async def test_status_group_event_is_forwarded(self):
        communicator, connected, _ = await self.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=1)

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "anpr.gate.1",
            {
                "type": "anpr.status",
                "payload": {
                    "gate_id": 1,
                    "fps": 10,
                    "vehicle_count": 3,
                },
            },
        )
        response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "status")
        self.assertEqual(response["data"]["vehicle_count"], 3)
        await communicator.disconnect()

    async def test_detection_group_event_is_forwarded(self):
        communicator, connected, _ = await self.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=1)

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "anpr.gate.1",
            {
                "type": "anpr.detection",
                "payload": {
                    "gate_id": 1,
                    "record_id": 102,
                    "plate": "KA02MN1826",
                    "authorized": False,
                },
            },
        )
        response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "detection")
        self.assertEqual(response["data"]["plate"], "KA02MN1826")
        await communicator.disconnect()