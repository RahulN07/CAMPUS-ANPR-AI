"""Authenticated WebSocket consumers for live ANPR monitoring."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from config.websocket_auth import ANPR_WEBSOCKET_SUBPROTOCOL

from .live_transport import gate_live_keys, get_live_transport


logger = logging.getLogger(__name__)

WS_CLOSE_BAD_REQUEST = 4400
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_GATE_NOT_FOUND = 4404
WS_CLOSE_SERVICE_UNAVAILABLE = 1013


@database_sync_to_async
def active_gate_exists(gate_id: int) -> bool:
    from access_management.models import Gate

    return Gate.objects.filter(
        pk=gate_id,
        is_active=True,
    ).exists()


class GateLiveConsumer(AsyncJsonWebsocketConsumer):
    """Read-only stream of status and detection events for one active gate."""

    gate_id: int | None = None
    gate_group: str | None = None

    async def connect(self) -> None:
        user = self.scope.get("user")
        if (
            user is None
            or not user.is_authenticated
            or not user.is_active
        ):
            await self.close(code=WS_CLOSE_UNAUTHORIZED)
            return

        raw_gate_id = (
            self.scope.get("url_route", {})
            .get("kwargs", {})
            .get("gate_id")
        )

        try:
            gate_id = int(raw_gate_id)
            if gate_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await self.close(code=WS_CLOSE_BAD_REQUEST)
            return

        if not await active_gate_exists(gate_id):
            await self.close(code=WS_CLOSE_GATE_NOT_FOUND)
            return

        keys = gate_live_keys(gate_id)
        self.gate_id = gate_id
        self.gate_group = keys.group
        self.live_transport = get_live_transport()

        try:
            await self.channel_layer.group_add(
                self.gate_group,
                self.channel_name,
            )
        except Exception as exc:
            logger.warning(
                "Unable to subscribe gate %s WebSocket to Redis: %s",
                gate_id,
                exc,
            )
            await self.close(code=WS_CLOSE_SERVICE_UNAVAILABLE)
            return

        requested_subprotocols = self.scope.get("subprotocols") or []
        accepted_subprotocol = (
            ANPR_WEBSOCKET_SUBPROTOCOL
            if ANPR_WEBSOCKET_SUBPROTOCOL in requested_subprotocols
            else None
        )
        await self.accept(subprotocol=accepted_subprotocol)
        await self._send_snapshot()

    async def disconnect(self, close_code: int) -> None:
        if not self.gate_group:
            return

        try:
            await self.channel_layer.group_discard(
                self.gate_group,
                self.channel_name,
            )
        except Exception as exc:
            logger.debug(
                "Unable to discard gate %s WebSocket group: %s",
                self.gate_id,
                exc,
            )

    async def receive_json(
        self,
        content: Any,
        **kwargs,
    ) -> None:
        if not isinstance(content, dict):
            await self._send_error(
                code="invalid_message",
                message="WebSocket messages must be JSON objects.",
            )
            return

        message_type = content.get("type")

        if message_type == "ping":
            await self.send_json(
                {
                    "type": "pong",
                    "data": {
                        "server_time": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            return

        if message_type == "get_status":
            await self._send_snapshot()
            return

        await self._send_error(
            code="unsupported_message",
            message="Only ping and get_status messages are supported.",
        )

    async def anpr_status(self, event: dict[str, Any]) -> None:
        await self.send_json(
            {
                "type": "status",
                "data": self._event_payload(event),
            }
        )

    async def anpr_detection(self, event: dict[str, Any]) -> None:
        await self.send_json(
            {
                "type": "detection",
                "data": self._event_payload(event),
            }
        )

    async def _send_snapshot(self) -> None:
        if self.gate_id is None:
            return

        status, events = await self._load_snapshot(self.gate_id)
        await self.send_json(
            {
                "type": "snapshot",
                "data": {
                    "gate_id": self.gate_id,
                    "status": status,
                    "recent_events": events,
                },
            }
        )

    async def _load_snapshot(
        self,
        gate_id: int,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        get_status = sync_to_async(
            self.live_transport.get_status,
            thread_sensitive=False,
        )
        recent_events = sync_to_async(
            self.live_transport.recent_events,
            thread_sensitive=False,
        )

        status, events = await asyncio.gather(
            get_status(gate_id),
            recent_events(gate_id, 25),
        )
        return status, events

    async def _send_error(
        self,
        code: str,
        message: str,
    ) -> None:
        await self.send_json(
            {
                "type": "error",
                "error": {
                    "code": code,
                    "message": message,
                },
            }
        )

    @staticmethod
    def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload")
        return payload if isinstance(payload, dict) else {}