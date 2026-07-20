"""
Thread-safe bounded queue for continuous camera frames.

When the queue reaches its maximum size, the oldest frame is
discarded before the newest frame is accepted. Camera capture
therefore never waits for downstream AI processing.
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, Optional, TypeVar

import numpy as np


QueueItem = TypeVar("QueueItem")


@dataclass(frozen=True, slots=True)
class FramePacket:
    """
    One captured frame and its immutable metadata.

    The numpy frame must be treated as read-only after this packet
    enters the queue. Consumers should copy it before drawing or
    changing pixels.
    """

    sequence: int
    gate_id: int
    frame: np.ndarray
    source_name: str = ""

    captured_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    captured_monotonic: float = field(
        default_factory=time.monotonic
    )


@dataclass(frozen=True, slots=True)
class FrameQueueStats:
    maxsize: int
    size: int
    accepted: int
    dropped: int


class DropOldestQueue(Generic[QueueItem]):
    """
    Bounded FIFO queue that always accepts the newest item.

    If full, put_latest() removes exactly one oldest item and then
    inserts the new item without blocking the producer.
    """

    def __init__(self, maxsize: int = 30):
        if maxsize <= 0:
            raise ValueError(
                "Queue maxsize must be greater than zero."
            )

        self._queue = queue.Queue(maxsize=maxsize)

        # Only producers need serialization around the
        # remove-oldest-and-insert-newest operation.
        self._producer_lock = threading.Lock()

        self._stats_lock = threading.Lock()
        self._accepted_count = 0
        self._dropped_count = 0

    @property
    def maxsize(self) -> int:
        return self._queue.maxsize

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def full(self) -> bool:
        return self._queue.full()

    def put_latest(
        self,
        item: QueueItem,
    ) -> Optional[QueueItem]:
        """
        Insert item immediately.

        Returns the discarded oldest item when the queue was full,
        otherwise returns None.
        """

        dropped_item = None

        with self._producer_lock:
            while True:
                try:
                    self._queue.put_nowait(item)
                    break
                except queue.Full:
                    try:
                        dropped_item = (
                            self._queue.get_nowait()
                        )
                    except queue.Empty:
                        # A consumer removed an item between the
                        # full check and get operation. Retry.
                        continue
                    else:
                        # Balance unfinished_tasks for the item that
                        # will never reach a consumer.
                        self._queue.task_done()

                        with self._stats_lock:
                            self._dropped_count += 1

            with self._stats_lock:
                self._accepted_count += 1

        return dropped_item

    def get(
        self,
        timeout: Optional[float] = None,
    ) -> QueueItem:
        return self._queue.get(
            block=True,
            timeout=timeout,
        )

    def get_nowait(self) -> QueueItem:
        return self._queue.get_nowait()

    def task_done(self) -> None:
        self._queue.task_done()

    def join(self) -> None:
        self._queue.join()

    def clear(self) -> int:
        """
        Remove all queued items.

        Returns the number of removed items. Shutdown cleanup is not
        counted as overflow dropping.
        """

        removed_count = 0

        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
                removed_count += 1

        return removed_count

    def stats(self) -> FrameQueueStats:
        with self._stats_lock:
            accepted = self._accepted_count
            dropped = self._dropped_count

        return FrameQueueStats(
            maxsize=self.maxsize,
            size=self.qsize(),
            accepted=accepted,
            dropped=dropped,
        )