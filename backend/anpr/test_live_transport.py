from __future__ import annotations

from unittest.mock import patch

import redis
from django.test import SimpleTestCase, override_settings

from anpr.live_transport import (
    MAX_JPEG_BYTES,
    AnprLiveTransport,
    decode_payload,
    encode_payload,
    gate_live_keys,
)


class InMemoryPipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def hset(self, *args, **kwargs):
        self.commands.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.commands.append(("expire", args, kwargs))
        return self

    def lpush(self, *args, **kwargs):
        self.commands.append(("lpush", args, kwargs))
        return self

    def ltrim(self, *args, **kwargs):
        self.commands.append(("ltrim", args, kwargs))
        return self

    def execute(self):
        results = []
        for name, args, kwargs in self.commands:
            results.append(getattr(self.client, name)(*args, **kwargs))
        self.commands.clear()
        return results


class InMemoryRedis:
    def __init__(self):
        self.hashes = {}
        self.values = {}
        self.lists = {}
        self.expirations = {}

    @staticmethod
    def _bytes(value):
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    def pipeline(self, transaction=True):
        return InMemoryPipeline(self)

    def ping(self):
        return True

    def hset(self, key, mapping):
        self.hashes[key] = {
            self._bytes(field): self._bytes(value)
            for field, value in mapping.items()
        }
        return len(mapping)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def set(self, key, value, ex=None):
        self.values[key] = self._bytes(value)
        if ex is not None:
            self.expirations[key] = ex
        return True

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True

    def lpush(self, key, value):
        values = self.lists.setdefault(key, [])
        values.insert(0, self._bytes(value))
        return len(values)

    def ltrim(self, key, start, end):
        values = self.lists.setdefault(key, [])
        self.lists[key] = values[start : end + 1]
        return True

    def lrange(self, key, start, end):
        return list(self.lists.get(key, [])[start : end + 1])

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            for store in (self.hashes, self.values, self.lists):
                if key in store:
                    del store[key]
                    deleted += 1
            self.expirations.pop(key, None)
        return deleted


class BrokenRedis:
    def ping(self):
        raise redis.ConnectionError("offline")

    def pipeline(self, transaction=True):
        raise redis.ConnectionError("offline")

    def hgetall(self, key):
        raise redis.ConnectionError("offline")

    def get(self, key):
        raise redis.ConnectionError("offline")

    def set(self, key, value, ex=None):
        raise redis.ConnectionError("offline")

    def lrange(self, key, start, end):
        raise redis.ConnectionError("offline")

    def delete(self, *keys):
        raise redis.ConnectionError("offline")


class RecordingChannelLayer:
    def __init__(self):
        self.messages = []

    async def group_send(self, group, message):
        self.messages.append((group, message))


@override_settings(
    REDIS_URL="redis://127.0.0.1:6379/0",
    ANPR_LIVE_FRAME_TTL_SECONDS=5,
    ANPR_LIVE_STATUS_TTL_SECONDS=15,
    ANPR_LIVE_EVENT_HISTORY_SIZE=3,
)
class LiveTransportTests(SimpleTestCase):
    def setUp(self):
        self.redis = InMemoryRedis()
        self.channel_layer = RecordingChannelLayer()
        self.transport = AnprLiveTransport(
            redis_client=self.redis,
            channel_layer=self.channel_layer,
        )

    def test_gate_keys_are_stable_and_scoped(self):
        keys = gate_live_keys(12)

        self.assertEqual(keys.gate_id, 12)
        self.assertEqual(
            keys.frame,
            "campus_anpr:live:gate:12:frame",
        )
        self.assertEqual(keys.group, "anpr.gate.12")

    def test_gate_id_must_be_positive(self):
        for value in (None, "bad", 0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    gate_live_keys(value)

    def test_payload_round_trip(self):
        encoded = encode_payload(
            {
                "gate_id": 1,
                "fps": 9.8,
                "authorized": True,
            }
        )

        self.assertEqual(
            decode_payload(encoded),
            {
                "gate_id": 1,
                "fps": 9.8,
                "authorized": True,
            },
        )

    def test_construction_does_not_open_redis_connection(self):
        with patch("anpr.live_transport.redis.Redis.from_url") as from_url:
            AnprLiveTransport(redis_url="redis://example.invalid:6379/0")
            from_url.assert_not_called()

    def test_publish_and_read_latest_frame(self):
        self.assertTrue(
            self.transport.publish_frame(
                gate_id=1,
                jpeg=b"jpeg-one",
                metadata={"fps": 10, "tracks": [{"track_id": 7}]},
            )
        )

        first = self.transport.get_latest_frame(1)
        self.assertIsNotNone(first)
        self.assertEqual(first.jpeg, b"jpeg-one")
        self.assertEqual(first.metadata["fps"], 10)
        self.assertEqual(first.metadata["tracks"][0]["track_id"], 7)

        self.assertTrue(
            self.transport.publish_frame(
                gate_id=1,
                jpeg=b"jpeg-two",
                metadata={"fps": 9},
            )
        )

        newest = self.transport.get_latest_frame(1)
        self.assertEqual(newest.jpeg, b"jpeg-two")
        self.assertEqual(newest.metadata["fps"], 9)
        self.assertGreater(newest.sequence, first.sequence)

        keys = gate_live_keys(1)
        self.assertEqual(self.redis.expirations[keys.frame], 5)

    def test_publish_frame_validates_image_bytes(self):
        for value in (b"", "not-bytes", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.transport.publish_frame(1, value)

        with self.assertRaises(ValueError):
            self.transport.publish_frame(
                1,
                b"x" * (MAX_JPEG_BYTES + 1),
            )

    def test_status_is_stored_and_broadcast(self):
        self.assertTrue(
            self.transport.publish_status(
                1,
                {
                    "state": "RUNNING",
                    "fps": 9.7,
                    "frame_queue_size": 0,
                },
            )
        )

        status = self.transport.get_status(1)
        self.assertEqual(status["state"], "RUNNING")
        self.assertEqual(status["gate_id"], 1)

        self.assertEqual(len(self.channel_layer.messages), 1)
        group, message = self.channel_layer.messages[0]
        self.assertEqual(group, "anpr.gate.1")
        self.assertEqual(message["type"], "anpr.status")
        self.assertEqual(message["payload"]["fps"], 9.7)

    def test_detection_history_is_bounded_and_newest_first(self):
        for index in range(5):
            self.assertTrue(
                self.transport.publish_detection(
                    1,
                    {
                        "record_id": index,
                        "plate": f"KA02AB{index:04d}",
                    },
                )
            )

        events = self.transport.recent_events(1, limit=10)
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [event["record_id"] for event in events],
            [4, 3, 2],
        )

        self.assertEqual(len(self.channel_layer.messages), 5)
        self.assertTrue(
            all(
                message["type"] == "anpr.detection"
                for _, message in self.channel_layer.messages
            )
        )

    def test_clear_gate_removes_live_data(self):
        self.transport.publish_frame(1, b"jpeg")
        self.transport.publish_status(1, {"state": "RUNNING"})
        self.transport.publish_detection(1, {"record_id": 10})

        self.assertTrue(self.transport.clear_gate(1))
        self.assertIsNone(self.transport.get_latest_frame(1))
        self.assertIsNone(self.transport.get_status(1))
        self.assertEqual(self.transport.recent_events(1), [])

    def test_stats_count_successful_publications(self):
        self.transport.publish_frame(1, b"jpeg")
        self.transport.publish_status(1, {"state": "RUNNING"})
        self.transport.publish_detection(1, {"record_id": 1})

        stats = self.transport.stats()
        self.assertEqual(stats.frames_published, 1)
        self.assertEqual(stats.statuses_published, 1)
        self.assertEqual(stats.events_published, 1)
        self.assertEqual(stats.redis_failures, 0)
        self.assertEqual(stats.broadcast_failures, 0)

    def test_redis_failures_do_not_escape(self):
        transport = AnprLiveTransport(
            redis_client=BrokenRedis(),
            channel_layer=self.channel_layer,
        )

        self.assertFalse(transport.ping())
        self.assertFalse(transport.publish_frame(1, b"jpeg"))
        self.assertIsNone(transport.get_latest_frame(1))
        self.assertFalse(
            transport.publish_status(1, {"state": "RUNNING"})
        )
        self.assertFalse(
            transport.publish_detection(1, {"record_id": 1})
        )
        self.assertEqual(transport.recent_events(1), [])
        self.assertFalse(transport.clear_gate(1))

        stats = transport.stats()
        self.assertEqual(stats.redis_failures, 7)
        self.assertIn("offline", stats.last_error)