"""Concurrency and failure-isolation tests for the live ANPR publisher."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase

from anpr.live_publisher import (
    AnprLivePublisher,
    LivePublisherConfig,
    LivePublisherState,
)


class FakeLiveTransport:
    """Thread-safe transport double with optional publication barriers."""

    def __init__(self) -> None:
        self.frames: list[tuple[int, bytes, dict]] = []
        self.statuses: list[tuple[int, dict]] = []
        self.detections: list[tuple[int, dict]] = []
        self.frame_started = threading.Event()
        self.detection_started = threading.Event()
        self.release_frames = threading.Event()
        self.release_detections = threading.Event()
        self.block_frames = False
        self.block_detections = False
        self.frame_result = True
        self.status_result = True
        self.detection_result = True
        self.frame_errors_remaining = 0
        self.status_errors_remaining = 0
        self.detection_errors_remaining = 0
        self._lock = threading.Lock()

    def publish_frame(self, gate_id, jpeg, metadata=None):
        self.frame_started.set()
        if self.block_frames:
            self.release_frames.wait(2.0)
        with self._lock:
            if self.frame_errors_remaining:
                self.frame_errors_remaining -= 1
                raise RuntimeError("frame transport offline")
            self.frames.append((gate_id, jpeg, dict(metadata or {})))
            return self.frame_result

    def publish_status(self, gate_id, status):
        with self._lock:
            if self.status_errors_remaining:
                self.status_errors_remaining -= 1
                raise RuntimeError("status transport offline")
            self.statuses.append((gate_id, dict(status)))
            return self.status_result

    def publish_detection(self, gate_id, detection):
        self.detection_started.set()
        if self.block_detections:
            self.release_detections.wait(2.0)
        with self._lock:
            if self.detection_errors_remaining:
                self.detection_errors_remaining -= 1
                raise RuntimeError("event transport offline")
            self.detections.append((gate_id, dict(detection)))
            return self.detection_result


class LivePublisherTests(SimpleTestCase):
    def make_publisher(
        self,
        transport: FakeLiveTransport | None = None,
        **config_overrides,
    ) -> tuple[AnprLivePublisher, FakeLiveTransport]:
        fake = transport or FakeLiveTransport()
        config = LivePublisherConfig(
            thread_name_prefix="test-live-publisher",
            **config_overrides,
        )
        publisher = AnprLivePublisher(
            gate_id=7,
            transport=fake,
            config=config,
        )
        self.addCleanup(self._stop_safely, publisher, fake)
        return publisher, fake

    @staticmethod
    def _stop_safely(
        publisher: AnprLivePublisher,
        transport: FakeLiveTransport,
    ) -> None:
        transport.release_frames.set()
        transport.release_detections.set()
        publisher.stop(drain=False, timeout=1.0)

    def test_configuration_and_gate_validation(self):
        with self.assertRaisesMessage(ValueError, "gate_id"):
            AnprLivePublisher(gate_id=0)
        with self.assertRaisesMessage(ValueError, "frame_queue_size"):
            LivePublisherConfig(frame_queue_size=0)
        with self.assertRaisesMessage(ValueError, "detection_queue_size"):
            LivePublisherConfig(detection_queue_size=0)
        with self.assertRaisesMessage(ValueError, "thread_name_prefix"):
            LivePublisherConfig(thread_name_prefix="  ")

    def test_construction_does_not_resolve_default_transport(self):
        with patch(
            "anpr.live_publisher.get_live_transport"
        ) as get_transport:
            publisher = AnprLivePublisher(gate_id=1)
            get_transport.assert_not_called()
            self.assertEqual(publisher.state, LivePublisherState.CREATED)
            self.assertTrue(publisher.stop())

    def test_lifecycle_is_single_use(self):
        publisher, _ = self.make_publisher()

        self.assertTrue(publisher.start())
        self.assertFalse(publisher.start())
        self.assertTrue(publisher.stop(timeout=1.0))
        self.assertEqual(publisher.state, LivePublisherState.STOPPED)
        self.assertEqual(publisher.stats().live_threads, 0)

        with self.assertRaisesMessage(RuntimeError, "cannot be restarted"):
            publisher.start()

    def test_latest_frame_replaces_stale_pending_frame(self):
        publisher, transport = self.make_publisher(frame_queue_size=1)
        transport.block_frames = True
        publisher.start()

        self.assertTrue(publisher.submit_frame(b"in-flight"))
        self.assertTrue(transport.frame_started.wait(1.0))
        self.assertTrue(publisher.submit_frame(b"stale"))
        self.assertTrue(publisher.submit_frame(b"newest", {"fps": 10}))

        transport.release_frames.set()
        self.assertTrue(publisher.wait_until_idle(1.0))

        self.assertEqual(
            [frame[1] for frame in transport.frames],
            [b"in-flight", b"newest"],
        )
        self.assertEqual(transport.frames[-1][2], {"fps": 10})
        stats = publisher.stats()
        self.assertEqual(stats.frames_submitted, 3)
        self.assertEqual(stats.frames_published, 2)
        self.assertEqual(stats.frames_dropped, 1)

    def test_status_updates_are_coalesced_to_latest_snapshot(self):
        publisher, transport = self.make_publisher()
        transport.block_detections = True
        publisher.start()

        self.assertTrue(publisher.submit_detection({"record_id": 1}))
        self.assertTrue(transport.detection_started.wait(1.0))
        self.assertTrue(publisher.submit_status({"fps": 1}))
        self.assertTrue(publisher.submit_status({"fps": 5}))
        self.assertTrue(publisher.submit_status({"fps": 10}))

        transport.release_detections.set()
        self.assertTrue(publisher.wait_until_idle(1.0))

        self.assertEqual(transport.statuses, [(7, {"fps": 10})])
        stats = publisher.stats()
        self.assertEqual(stats.statuses_submitted, 3)
        self.assertEqual(stats.statuses_published, 1)
        self.assertEqual(stats.statuses_coalesced, 2)

    def test_detection_queue_rejects_full_without_blocking(self):
        publisher, transport = self.make_publisher(detection_queue_size=1)
        transport.block_detections = True
        publisher.start()

        self.assertTrue(publisher.submit_detection({"record_id": 1}))
        self.assertTrue(transport.detection_started.wait(1.0))
        self.assertTrue(publisher.submit_detection({"record_id": 2}))

        started = time.perf_counter()
        self.assertFalse(publisher.submit_detection({"record_id": 3}))
        self.assertLess(time.perf_counter() - started, 0.1)

        transport.release_detections.set()
        self.assertTrue(publisher.wait_until_idle(1.0))
        self.assertEqual(
            [item[1]["record_id"] for item in transport.detections],
            [1, 2],
        )
        self.assertEqual(publisher.stats().detections_rejected_full, 1)

    def test_payloads_are_routed_with_configured_gate(self):
        publisher, transport = self.make_publisher()
        publisher.start()

        self.assertTrue(publisher.submit_frame(b"jpeg", {"sequence": 9}))
        self.assertTrue(publisher.submit_status({"state": "RUNNING"}))
        self.assertTrue(
            publisher.submit_detection(
                {"record_id": 42, "plate": "KA02MM9091"}
            )
        )
        self.assertTrue(publisher.wait_until_idle(1.0))

        self.assertEqual(transport.frames, [(7, b"jpeg", {"sequence": 9})])
        self.assertEqual(transport.statuses, [(7, {"state": "RUNNING"})])
        self.assertEqual(
            transport.detections,
            [(7, {"record_id": 42, "plate": "KA02MM9091"})],
        )

    def test_false_transport_results_are_counted_as_failures(self):
        publisher, transport = self.make_publisher()
        transport.frame_result = False
        transport.status_result = False
        transport.detection_result = False
        publisher.start()

        publisher.submit_frame(b"jpeg")
        publisher.submit_status({"fps": 10})
        publisher.submit_detection({"record_id": 4})
        self.assertTrue(publisher.wait_until_idle(1.0))

        stats = publisher.stats()
        self.assertEqual(stats.frames_failed, 1)
        self.assertEqual(stats.statuses_failed, 1)
        self.assertEqual(stats.detections_failed, 1)
        self.assertEqual(stats.frames_published, 0)
        self.assertEqual(stats.statuses_published, 0)
        self.assertEqual(stats.detections_published, 0)

    def test_transport_exception_is_isolated_and_thread_continues(self):
        publisher, transport = self.make_publisher()
        transport.frame_errors_remaining = 1
        publisher.start()

        self.assertTrue(publisher.submit_frame(b"bad"))
        self.assertTrue(publisher.wait_until_idle(1.0))
        self.assertTrue(publisher.submit_frame(b"good"))
        self.assertTrue(publisher.wait_until_idle(1.0))

        stats = publisher.stats()
        self.assertEqual(stats.frames_failed, 1)
        self.assertEqual(stats.frames_published, 1)
        self.assertIn("RuntimeError", stats.last_error)
        self.assertEqual(transport.frames[-1][1], b"good")

    def test_wait_until_idle_includes_transport_call_in_flight(self):
        publisher, transport = self.make_publisher()
        transport.block_frames = True
        publisher.start()

        publisher.submit_frame(b"blocked")
        self.assertTrue(transport.frame_started.wait(1.0))
        self.assertEqual(publisher.stats().in_flight, 1)
        self.assertFalse(publisher.wait_until_idle(0.02))

        transport.release_frames.set()
        self.assertTrue(publisher.wait_until_idle(1.0))
        self.assertEqual(publisher.stats().in_flight, 0)

    def test_stop_without_drain_discards_only_pending_work(self):
        publisher, transport = self.make_publisher()
        transport.block_frames = True
        transport.block_detections = True
        publisher.start()

        publisher.submit_frame(b"frame-in-flight")
        publisher.submit_detection({"record_id": 1})
        self.assertTrue(transport.frame_started.wait(1.0))
        self.assertTrue(transport.detection_started.wait(1.0))

        publisher.submit_frame(b"frame-pending")
        publisher.submit_detection({"record_id": 2})
        publisher.submit_status({"fps": 10})

        self.assertFalse(publisher.stop(drain=False, timeout=0.02))
        stats = publisher.stats()
        self.assertEqual(stats.state, LivePublisherState.STOPPING)
        self.assertEqual(stats.discarded_on_stop, 3)

        transport.release_frames.set()
        transport.release_detections.set()
        self.assertTrue(publisher.stop(drain=False, timeout=1.0))
        self.assertEqual(publisher.state, LivePublisherState.STOPPED)

    def test_submissions_are_rejected_when_not_running(self):
        publisher, _ = self.make_publisher()

        self.assertFalse(publisher.submit_frame(b"jpeg"))
        self.assertFalse(publisher.submit_status({"fps": 10}))
        self.assertFalse(publisher.submit_detection({"record_id": 1}))
        self.assertTrue(publisher.stop())
        self.assertFalse(publisher.submit_frame(b"jpeg"))

    def test_frame_input_and_timeouts_are_validated(self):
        publisher, _ = self.make_publisher()
        with self.assertRaisesMessage(ValueError, "jpeg"):
            publisher.submit_frame(b"")
        with self.assertRaisesMessage(ValueError, "timeout"):
            publisher.wait_until_idle(-1)
        with self.assertRaisesMessage(ValueError, "timeout"):
            publisher.stop(timeout=-1)
