"""Redis-backed live transport shared by CCTV workers and Django ASGI."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import redis
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings


logger = logging.getLogger(__name__)

LIVE_KEY_PREFIX = "campus_anpr:live"
MAX_JPEG_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GateLiveKeys:
    gate_id: int
    frame: str
    status: str
    events: str
    group: str


@dataclass(frozen=True, slots=True)
class LiveFrameSnapshot:
    gate_id: int
    sequence: int
    published_at: str
    jpeg: bytes
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LiveTransportStats:
    frames_published: int
    statuses_published: int
    events_published: int
    redis_failures: int
    broadcast_failures: int
    last_error: str


def gate_live_keys(gate_id: int) -> GateLiveKeys:
    try:
        normalized_gate_id = int(gate_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("gate_id must be a positive integer") from exc

    if normalized_gate_id <= 0:
        raise ValueError("gate_id must be a positive integer")

    base = f"{LIVE_KEY_PREFIX}:gate:{normalized_gate_id}"
    return GateLiveKeys(
        gate_id=normalized_gate_id,
        frame=f"{base}:frame",
        status=f"{base}:status",
        events=f"{base}:events",
        group=f"anpr.gate.{normalized_gate_id}",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def encode_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        default=_json_default,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def decode_payload(value: bytes | str | None) -> dict[str, Any]:
    if value in (None, b"", ""):
        return {}

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("live payload must contain a JSON object")
    return decoded


class AnprLiveTransport:
    """
    Synchronous, thread-safe Redis access for the live ANPR bridge.

    The client is lazy so management commands, migrations, and tests do not
    require Redis merely to import Django. Calls return ``False`` on transport
    failures, allowing the camera pipeline to continue independently.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        redis_client: redis.Redis | None = None,
        channel_layer=None,
    ) -> None:
        self._redis_url = redis_url or settings.REDIS_URL
        self._redis = redis_client
        self._channel_layer = channel_layer
        self._client_lock = threading.Lock()
        self._sequence_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._last_sequence = 0
        self._frames_published = 0
        self._statuses_published = 0
        self._events_published = 0
        self._redis_failures = 0
        self._broadcast_failures = 0
        self._last_error = ""

    def _client(self) -> redis.Redis:
        if self._redis is not None:
            return self._redis

        with self._client_lock:
            if self._redis is None:
                self._redis = redis.Redis.from_url(
                    self._redis_url,
                    decode_responses=False,
                    socket_connect_timeout=0.25,
                    socket_timeout=0.25,
                    health_check_interval=30,
                    retry_on_timeout=False,
                )
        return self._redis

    def ping(self) -> bool:
        try:
            return bool(self._client().ping())
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return False

    def publish_frame(
        self,
        gate_id: int,
        jpeg: bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        keys = gate_live_keys(gate_id)

        if not isinstance(jpeg, bytes) or not jpeg:
            raise ValueError("jpeg must contain encoded image bytes")
        if len(jpeg) > MAX_JPEG_BYTES:
            raise ValueError("jpeg exceeds the live-frame size limit")

        published_at = datetime.now(timezone.utc).isoformat()
        sequence = self._next_sequence()
        frame_metadata = dict(metadata or {})
        frame_metadata.update(
            {
                "gate_id": keys.gate_id,
                "sequence": sequence,
                "published_at": published_at,
            }
        )

        try:
            pipeline = self._client().pipeline(transaction=True)
            pipeline.hset(
                keys.frame,
                mapping={
                    "jpeg": jpeg,
                    "metadata": encode_payload(frame_metadata),
                    "sequence": str(sequence),
                    "published_at": published_at,
                },
            )
            pipeline.expire(
                keys.frame,
                settings.ANPR_LIVE_FRAME_TTL_SECONDS,
            )
            pipeline.execute()
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return False

        self._increment("frames")
        return True

    def get_latest_frame(self, gate_id: int) -> LiveFrameSnapshot | None:
        keys = gate_live_keys(gate_id)

        try:
            values = self._client().hgetall(keys.frame)
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return None

        if not values:
            return None

        try:
            jpeg = values.get(b"jpeg") or values.get("jpeg")
            metadata_value = values.get(b"metadata") or values.get("metadata")
            sequence_value = values.get(b"sequence") or values.get("sequence")
            published_value = values.get(b"published_at") or values.get("published_at")

            if not isinstance(jpeg, bytes) or not jpeg:
                return None

            if isinstance(sequence_value, bytes):
                sequence_value = sequence_value.decode("ascii")
            if isinstance(published_value, bytes):
                published_value = published_value.decode("utf-8")

            return LiveFrameSnapshot(
                gate_id=keys.gate_id,
                sequence=int(sequence_value),
                published_at=str(published_value),
                jpeg=jpeg,
                metadata=decode_payload(metadata_value),
            )
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._record_redis_failure(exc)
            return None

    def publish_status(
        self,
        gate_id: int,
        status: Mapping[str, Any],
    ) -> bool:
        keys = gate_live_keys(gate_id)
        payload = dict(status)
        payload.update(
            {
                "gate_id": keys.gate_id,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        if not self._set_json(
            keys.status,
            payload,
            settings.ANPR_LIVE_STATUS_TTL_SECONDS,
        ):
            return False

        self._increment("statuses")
        self._broadcast(keys.group, "anpr.status", payload)
        return True

    def get_status(self, gate_id: int) -> dict[str, Any] | None:
        keys = gate_live_keys(gate_id)
        try:
            value = self._client().get(keys.status)
            return decode_payload(value) if value else None
        except (
            redis.RedisError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            self._record_redis_failure(exc)
            return None

    def publish_detection(
        self,
        gate_id: int,
        detection: Mapping[str, Any],
    ) -> bool:
        keys = gate_live_keys(gate_id)
        payload = dict(detection)
        payload.update(
            {
                "gate_id": keys.gate_id,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        encoded = encode_payload(payload)

        try:
            pipeline = self._client().pipeline(transaction=True)
            pipeline.lpush(keys.events, encoded)
            pipeline.ltrim(
                keys.events,
                0,
                settings.ANPR_LIVE_EVENT_HISTORY_SIZE - 1,
            )
            pipeline.expire(keys.events, 24 * 60 * 60)
            pipeline.execute()
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return False

        self._increment("events")
        self._broadcast(keys.group, "anpr.detection", payload)
        return True

    def recent_events(
        self,
        gate_id: int,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        keys = gate_live_keys(gate_id)
        safe_limit = max(
            1,
            min(int(limit), settings.ANPR_LIVE_EVENT_HISTORY_SIZE),
        )

        try:
            values = self._client().lrange(keys.events, 0, safe_limit - 1)
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return []

        events: list[dict[str, Any]] = []
        for value in values:
            try:
                events.append(decode_payload(value))
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                logger.warning("Ignoring invalid live ANPR event: %s", exc)
        return events

    def clear_gate(self, gate_id: int) -> bool:
        keys = gate_live_keys(gate_id)
        try:
            self._client().delete(keys.frame, keys.status, keys.events)
            return True
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return False

    def stats(self) -> LiveTransportStats:
        with self._stats_lock:
            return LiveTransportStats(
                frames_published=self._frames_published,
                statuses_published=self._statuses_published,
                events_published=self._events_published,
                redis_failures=self._redis_failures,
                broadcast_failures=self._broadcast_failures,
                last_error=self._last_error,
            )

    def _set_json(
        self,
        key: str,
        payload: Mapping[str, Any],
        ttl_seconds: int,
    ) -> bool:
        try:
            self._client().set(
                key,
                encode_payload(payload),
                ex=ttl_seconds,
            )
            return True
        except redis.RedisError as exc:
            self._record_redis_failure(exc)
            return False

    def _next_sequence(self) -> int:
        """Return a strictly increasing frame sequence on every platform."""

        candidate = time.time_ns()
        with self._sequence_lock:
            if candidate <= self._last_sequence:
                candidate = self._last_sequence + 1
            self._last_sequence = candidate
            return candidate

    def _broadcast(
        self,
        group: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> bool:
        try:
            channel_layer = self._channel_layer or get_channel_layer()
            if channel_layer is None:
                raise RuntimeError("Django channel layer is not configured")

            async_to_sync(channel_layer.group_send)(
                group,
                {
                    "type": event_type,
                    "payload": dict(payload),
                },
            )
            return True
        except Exception as exc:  # Broadcasting must never stop camera work.
            with self._stats_lock:
                self._broadcast_failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Live ANPR broadcast failed: %s", exc)
            return False

    def _increment(self, counter: str) -> None:
        with self._stats_lock:
            if counter == "frames":
                self._frames_published += 1
            elif counter == "statuses":
                self._statuses_published += 1
            elif counter == "events":
                self._events_published += 1

    def _record_redis_failure(self, exc: BaseException) -> None:
        with self._stats_lock:
            self._redis_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Live ANPR Redis operation failed: %s", exc)


_default_transport: AnprLiveTransport | None = None
_default_transport_lock = threading.Lock()


def get_live_transport() -> AnprLiveTransport:
    global _default_transport

    if _default_transport is not None:
        return _default_transport

    with _default_transport_lock:
        if _default_transport is None:
            _default_transport = AnprLiveTransport()
    return _default_transport