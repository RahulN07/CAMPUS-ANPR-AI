from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from access_management.models import Gate
from accounts.models import User
from anpr.consumers import (
    WS_CLOSE_GATE_NOT_FOUND,
    WS_CLOSE_UNAUTHORIZED,
)
from config.asgi import application
from config.websocket_auth import extract_jwt_from_subprotocols


IN_MEMORY_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


class FakeLiveTransport:
    def get_status(self, gate_id):
        return {
            "gate_id": gate_id,
            "state": "RUNNING",
            "fps": 10,
        }

    def recent_events(self, gate_id, limit):
        return []


@override_settings(
    CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS,
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
)
class WebSocketAuthenticationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create(
            username="live_viewer",
            role=User.Role.VIEWER,
            is_active=True,
        )
        self.gate = Gate.objects.create(
            name="WebSocket Test Gate",
            gate_type=Gate.GateType.ENTRY,
            is_active=True,
        )
        self.inactive_user = User.objects.create(
            username="inactive_live_viewer",
            role=User.Role.VIEWER,
            is_active=False,
        )
        self.inactive_gate = Gate.objects.create(
            name="Inactive WebSocket Test Gate",
            gate_type=Gate.GateType.EXIT,
            is_active=False,
        )
        self.transport = FakeLiveTransport()

    def access_token(self, user=None):
        return str(AccessToken.for_user(user or self.user))

    def communicator(
        self,
        token=None,
        origin=b"http://localhost:5173",
        gate_id=None,
    ):
        subprotocols = ["anpr.v1"]
        if token is not None:
            subprotocols.append(token)

        return WebsocketCommunicator(
            application,
            (
                f"/ws/anpr/gates/"
                f"{gate_id or self.gate.id}/"
            ),
            headers=[(b"origin", origin)],
            subprotocols=subprotocols,
        )

    async def connect(self, communicator):
        with patch(
            "anpr.consumers.get_live_transport",
            return_value=self.transport,
        ):
            return await communicator.connect(timeout=2)

    def test_token_is_extracted_from_versioned_subprotocols(self):
        token = self.access_token()

        self.assertEqual(
            extract_jwt_from_subprotocols(
                {
                    "subprotocols": [
                        "anpr.v1",
                        token,
                    ]
                }
            ),
            token,
        )

    async def test_valid_token_connects_through_real_asgi_stack(self):
        communicator = self.communicator(token=self.access_token())
        connected, subprotocol = await self.connect(communicator)

        self.assertTrue(connected)
        self.assertEqual(subprotocol, "anpr.v1")

        snapshot = await communicator.receive_json_from(timeout=2)
        self.assertEqual(snapshot["type"], "snapshot")
        self.assertEqual(snapshot["data"]["gate_id"], self.gate.id)
        self.assertEqual(snapshot["data"]["status"]["state"], "RUNNING")

        await communicator.disconnect()

    async def test_missing_token_is_rejected(self):
        communicator = self.communicator(token=None)
        connected, close_code = await self.connect(communicator)

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_invalid_token_is_rejected(self):
        communicator = self.communicator(token="aaa.bbb.ccc")
        connected, close_code = await self.connect(communicator)

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_expired_token_is_rejected(self):
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))

        communicator = self.communicator(token=str(token))
        connected, close_code = await self.connect(communicator)

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_inactive_user_is_rejected(self):
        communicator = self.communicator(
            token=self.access_token(self.inactive_user)
        )
        connected, close_code = await self.connect(communicator)

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_UNAUTHORIZED)

    async def test_inactive_gate_is_rejected(self):
        communicator = self.communicator(
            token=self.access_token(),
            gate_id=self.inactive_gate.id,
        )
        connected, close_code = await self.connect(communicator)

        self.assertFalse(connected)
        self.assertEqual(close_code, WS_CLOSE_GATE_NOT_FOUND)

    async def test_untrusted_origin_is_rejected(self):
        communicator = self.communicator(
            token=self.access_token(),
            origin=b"https://untrusted.example",
        )
        connected, _ = await self.connect(communicator)

        self.assertFalse(connected)