"""Non-blocking bridge from the camera pipeline to the live transport.

The tracking loop must never wait for Redis or Django Channels.  This service
therefore accepts frames, statuses, and detections using only short in-memory
critical sections and performs all transport I/O on background threads.

Frames are lossy by design: when the frame buffer is full, the oldest frame is
discarded so the live monitor stays close to real time.  Detection events are
kept in a separate bounded queue and are never displaced by video frames.
Statuses are coalesced because only the newest status snapshot is useful.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from anpr.live_transport import get_live_transport


logger = logging.getLogger(__name__)


class LivePublisherState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class LivePublisherConfig:
    """Capacity and thread naming for one gate publisher."""

    frame_queue_size: int = 1
    detection_queue_size: int = 100
    thread_name_prefix: str = "anpr-live-publisher"

    def __post_init__(self) -> None:
        if not 1 <= self.frame_queue_size <= 30:
            raise ValueError("frame_queue_size must be between 1 and 30")
        if not 1 <= self.detection_queue_size <= 10000:
            raise ValueError(
                "detection_queue_size must be between 1 and 10000"
            )
        if not self.thread_name_prefix.strip():
            raise ValueError("thread_name_prefix cannot be empty")


@dataclass(frozen=True, slots=True)
class LivePublisherStats:
    state: LivePublisherState
    frames_submitted: int
    frames_published: int
    frames_dropped: int
    frames_failed: int
    statuses_submitted: int
    statuses_published: int
    statuses_coalesced: int
    statuses_failed: int
    detections_submitted: int
    detections_published: int
    detections_rejected_full: int
    detections_failed: int
    discarded_on_stop: int
    pending_frames: int
    pending_status: bool
    pending_detections: int
    in_flight: int
    live_threads: int
    last_error: str


class LiveTransport(Protocol):
    def publish_frame(
        self,
        gate_id: int,
        jpeg: bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool: ...

    def publish_status(
        self,
        gate_id: int,
        status: Mapping[str, Any],
    ) -> bool: ...

    def publish_detection(
        self,
        gate_id: int,
        detection: Mapping[str, Any],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _FramePublication:
    jpeg: bytes
    metadata: dict[str, Any]


class AnprLivePublisher:
    """Publish live gate data without blocking camera or tracking work."""

    def __init__(
        self,
        *,
        gate_id: int,
        transport: LiveTransport | None = None,
        config: LivePublisherConfig | None = None,
    ) -> None:
        try:
            normalized_gate_id = int(gate_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("gate_id must be a positive integer") from exc
        if normalized_gate_id <= 0:
            raise ValueError("gate_id must be a positive integer")

        self.gate_id = normalized_gate_id
        self.config = config or LivePublisherConfig()
        self._transport = transport

        self._condition = threading.Condition(threading.RLock())
        self._state = LivePublisherState.CREATED
        self._frames: deque[_FramePublication] = deque()
        self._detections: deque[dict[str, Any]] = deque()
        self._status: dict[str, Any] | None = None
        self._drain_on_stop = True
        self._threads: list[threading.Thread] = []
        self._live_threads = 0
        self._in_flight = 0

        self._frames_submitted = 0
        self._frames_published = 0
        self._frames_dropped = 0
        self._frames_failed = 0
        self._statuses_submitted = 0
        self._statuses_published = 0
        self._statuses_coalesced = 0
        self._statuses_failed = 0
        self._detections_submitted = 0
        self._detections_published = 0
        self._detections_rejected_full = 0
        self._detections_failed = 0
        self._discarded_on_stop = 0
        self._last_error = ""

    @property
    def state(self) -> LivePublisherState:
        with self._condition:
            return self._state

    def start(self) -> bool:
        """Start the frame and control publishers exactly once."""

        with self._condition:
            if self._state is LivePublisherState.RUNNING:
                return False
            if self._state in (
                LivePublisherState.STOPPING,
                LivePublisherState.STOPPED,
            ):
                raise RuntimeError("a stopped live publisher cannot be restarted")

            # Resolve the default lazily. Constructing this service still does
            # not open a Redis connection; the transport connects on publish.
            if self._transport is None:
                self._transport = get_live_transport()

            self._state = LivePublisherState.RUNNING
            self._threads = [
                threading.Thread(
                    target=self._frame_loop,
                    name=f"{self.config.thread_name_prefix}-frames",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._control_loop,
                    name=f"{self.config.thread_name_prefix}-control",
                    daemon=True,
                ),
            ]
            for thread in self._threads:
                thread.start()
            self._condition.notify_all()
            return True

    def submit_frame(
        self,
        jpeg: bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        """Accept an encoded frame immediately, dropping stale frames."""

        if not isinstance(jpeg, bytes) or not jpeg:
            raise ValueError("jpeg must contain encoded image bytes")

        publication = _FramePublication(
            jpeg=jpeg,
            metadata=dict(metadata or {}),
        )
        with self._condition:
            if self._state is not LivePublisherState.RUNNING:
                return False

            while len(self._frames) >= self.config.frame_queue_size:
                self._frames.popleft()
                self._frames_dropped += 1
            self._frames.append(publication)
            self._frames_submitted += 1
            self._condition.notify_all()
            return True

    def submit_status(self, status: Mapping[str, Any]) -> bool:
        """Replace the pending status snapshot without waiting."""

        payload = dict(status)
        with self._condition:
            if self._state is not LivePublisherState.RUNNING:
                return False
            if self._status is not None:
                self._statuses_coalesced += 1
            self._status = payload
            self._statuses_submitted += 1
            self._condition.notify_all()
            return True

    def submit_detection(self, detection: Mapping[str, Any]) -> bool:
        """Queue a detection without waiting; reject if its queue is full."""

        payload = dict(detection)
        with self._condition:
            if self._state is not LivePublisherState.RUNNING:
                return False
            if len(self._detections) >= self.config.detection_queue_size:
                self._detections_rejected_full += 1
                return False
            self._detections.append(payload)
            self._detections_submitted += 1
            self._condition.notify_all()
            return True

    def stop(
        self,
        *,
        drain: bool = True,
        timeout: float | None = 5.0,
    ) -> bool:
        """Stop both publishers, optionally draining accepted publications."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative")

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._state is LivePublisherState.STOPPED:
                return True
            if self._state is LivePublisherState.CREATED:
                self._state = LivePublisherState.STOPPED
                self._condition.notify_all()
                return True

            self._state = LivePublisherState.STOPPING
            self._drain_on_stop = bool(drain)
            if not drain:
                discarded = len(self._frames) + len(self._detections)
                if self._status is not None:
                    discarded += 1
                self._frames.clear()
                self._detections.clear()
                self._status = None
                self._discarded_on_stop += discarded
            self._condition.notify_all()

        for thread in self._threads:
            remaining = self._remaining(deadline)
            thread.join(remaining)

        with self._condition:
            if any(thread.is_alive() for thread in self._threads):
                return False
            self._state = LivePublisherState.STOPPED
            self._condition.notify_all()
            return True

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Wait until all currently accepted publications finish."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative")
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            while self._has_pending_unlocked():
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def stats(self) -> LivePublisherStats:
        with self._condition:
            return LivePublisherStats(
                state=self._state,
                frames_submitted=self._frames_submitted,
                frames_published=self._frames_published,
                frames_dropped=self._frames_dropped,
                frames_failed=self._frames_failed,
                statuses_submitted=self._statuses_submitted,
                statuses_published=self._statuses_published,
                statuses_coalesced=self._statuses_coalesced,
                statuses_failed=self._statuses_failed,
                detections_submitted=self._detections_submitted,
                detections_published=self._detections_published,
                detections_rejected_full=self._detections_rejected_full,
                detections_failed=self._detections_failed,
                discarded_on_stop=self._discarded_on_stop,
                pending_frames=len(self._frames),
                pending_status=self._status is not None,
                pending_detections=len(self._detections),
                in_flight=self._in_flight,
                live_threads=self._live_threads,
                last_error=self._last_error,
            )

    def _frame_loop(self) -> None:
        self._thread_started()
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._frames)
                        or self._state is LivePublisherState.STOPPING
                    )
                    if not self._frames:
                        if self._state is LivePublisherState.STOPPING:
                            return
                        continue
                    publication = self._frames.popleft()
                    self._in_flight += 1

                try:
                    self._publish_frame(publication)
                finally:
                    self._publication_finished()
        finally:
            self._thread_stopped()

    def _control_loop(self) -> None:
        self._thread_started()
        try:
            while True:
                kind: str
                payload: dict[str, Any]
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._detections)
                        or self._status is not None
                        or self._state is LivePublisherState.STOPPING
                    )

                    # Detection events have priority over replaceable status.
                    if self._detections:
                        kind = "detection"
                        payload = self._detections.popleft()
                    elif self._status is not None:
                        kind = "status"
                        payload = self._status
                        self._status = None
                    elif self._state is LivePublisherState.STOPPING:
                        return
                    else:
                        continue
                    self._in_flight += 1

                try:
                    self._publish_control(kind, payload)
                finally:
                    self._publication_finished()
        finally:
            self._thread_stopped()

    def _publish_frame(self, publication: _FramePublication) -> None:
        try:
            transport = self._require_transport()
            published = transport.publish_frame(
                self.gate_id,
                publication.jpeg,
                publication.metadata,
            )
        except Exception as exc:  # Camera work must survive any adapter error.
            self._record_failure("frame", exc)
            return

        with self._condition:
            if published:
                self._frames_published += 1
            else:
                self._frames_failed += 1
            self._condition.notify_all()

    def _publish_control(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            transport = self._require_transport()
            if kind == "detection":
                published = transport.publish_detection(self.gate_id, payload)
            else:
                published = transport.publish_status(self.gate_id, payload)
        except Exception as exc:  # Worker callbacks must survive adapter bugs.
            self._record_failure(kind, exc)
            return

        with self._condition:
            if kind == "detection":
                if published:
                    self._detections_published += 1
                else:
                    self._detections_failed += 1
            else:
                if published:
                    self._statuses_published += 1
                else:
                    self._statuses_failed += 1
            self._condition.notify_all()

    def _record_failure(self, kind: str, exc: BaseException) -> None:
        with self._condition:
            if kind == "frame":
                self._frames_failed += 1
            elif kind == "detection":
                self._detections_failed += 1
            else:
                self._statuses_failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._condition.notify_all()
        logger.exception("Live %s publication failed", kind)

    def _thread_started(self) -> None:
        with self._condition:
            self._live_threads += 1
            self._condition.notify_all()

    def _thread_stopped(self) -> None:
        with self._condition:
            self._live_threads -= 1
            self._condition.notify_all()

    def _publication_finished(self) -> None:
        with self._condition:
            self._in_flight -= 1
            self._condition.notify_all()

    def _has_pending_unlocked(self) -> bool:
        return bool(
            self._frames
            or self._detections
            or self._status is not None
            or self._in_flight
        )

    def _require_transport(self) -> LiveTransport:
        if self._transport is None:
            raise RuntimeError("live transport is unavailable")
        return self._transport

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())