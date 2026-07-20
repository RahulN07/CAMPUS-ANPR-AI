"""Bounded, non-blocking worker pool for finalized vehicle tracks.

Camera and tracking threads only call ``submit``.  A full queue is reported
immediately instead of blocking the camera.  ``processor_factory`` can create
one independent OCR processor per worker when the processing layer is added.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

from django.db import close_old_connections


logger = logging.getLogger(__name__)

TaskType = TypeVar("TaskType")
Processor = Callable[[TaskType], Any]
ProcessorFactory = Callable[[], Processor]
SuccessCallback = Callable[[TaskType, Any], None]
ErrorCallback = Callable[[TaskType, BaseException], None]

_STOP = object()


class WorkerPoolState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class WorkerPoolConfig:
    worker_count: int = 5
    queue_size: int = 100
    thread_name_prefix: str = "anpr-vehicle-worker"
    manage_django_connections: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.worker_count <= 32:
            raise ValueError("worker_count must be between 1 and 32")
        if not 1 <= self.queue_size <= 10000:
            raise ValueError("queue_size must be between 1 and 10000")
        if not self.thread_name_prefix.strip():
            raise ValueError("thread_name_prefix cannot be empty")


@dataclass(frozen=True, slots=True)
class WorkerPoolStats:
    state: WorkerPoolState
    submitted: int
    completed: int
    failed: int
    rejected_full: int
    rejected_not_running: int
    discarded_on_stop: int
    queue_size: int
    queue_capacity: int
    in_flight: int
    live_workers: int
    last_error: str


class VehicleWorkerPool(Generic[TaskType]):
    """Fixed-size worker pool with a bounded, non-blocking input queue."""

    def __init__(
        self,
        *,
        processor: Processor | None = None,
        processor_factory: ProcessorFactory | None = None,
        config: WorkerPoolConfig | None = None,
        on_success: SuccessCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        if (processor is None) == (processor_factory is None):
            raise ValueError(
                "provide exactly one of processor or processor_factory"
            )

        self.config = config or WorkerPoolConfig()
        self._processor = processor
        self._processor_factory = processor_factory
        self._on_success = on_success
        self._on_error = on_error
        self._queue: queue.Queue[TaskType | object] = queue.Queue(
            maxsize=self.config.queue_size
        )
        self._condition = threading.Condition(threading.RLock())
        self._state = WorkerPoolState.CREATED
        self._threads: list[threading.Thread] = []
        self._stop_markers_sent = 0
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._rejected_full = 0
        self._rejected_not_running = 0
        self._discarded_on_stop = 0
        self._in_flight = 0
        self._live_workers = 0
        self._last_error = ""

    @property
    def state(self) -> WorkerPoolState:
        with self._condition:
            return self._state

    def start(self) -> bool:
        """Start workers once. Returns False when already running."""

        with self._condition:
            if self._state is WorkerPoolState.RUNNING:
                return False
            if self._state in (
                WorkerPoolState.STOPPING,
                WorkerPoolState.STOPPED,
            ):
                raise RuntimeError("a stopped worker pool cannot be restarted")

            self._state = WorkerPoolState.RUNNING
            for index in range(self.config.worker_count):
                thread = threading.Thread(
                    target=self._worker_loop,
                    name=f"{self.config.thread_name_prefix}-{index + 1}",
                    daemon=True,
                )
                self._threads.append(thread)
                thread.start()
            self._condition.notify_all()
            return True

    def submit(self, task: TaskType) -> bool:
        """Queue a task without ever waiting for capacity."""

        with self._condition:
            if self._state is not WorkerPoolState.RUNNING:
                self._rejected_not_running += 1
                return False

            try:
                self._queue.put_nowait(task)
            except queue.Full:
                self._rejected_full += 1
                return False

            self._submitted += 1
            self._condition.notify_all()
            return True

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Wait until both queued and in-flight work reaches zero."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative")

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._is_idle_unlocked():
                if deadline is None:
                    self._condition.wait()
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def stop(
        self,
        *,
        drain: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """Stop accepting tasks and terminate workers safely.

        With ``drain=True`` all accepted work finishes first.  If the timeout
        expires, workers remain alive in STOPPING state and a later call may
        wait again.  ``drain=False`` discards queued (not in-flight) tasks.
        """

        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative")

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._state is WorkerPoolState.STOPPED:
                return True
            if self._state is WorkerPoolState.CREATED:
                self._state = WorkerPoolState.STOPPED
                self._condition.notify_all()
                return True
            self._state = WorkerPoolState.STOPPING
            self._condition.notify_all()

        if not drain:
            self._discard_queued_tasks()

        remaining = self._remaining(deadline)
        if not self.wait_until_idle(remaining):
            return False

        # The queue is now empty.  A queue may be smaller than the worker
        # count, so shutdown markers are inserted with the caller's timeout
        # while workers consume them concurrently.
        markers_needed = len(self._threads) - self._stop_markers_sent
        for _ in range(markers_needed):
            remaining = self._remaining(deadline)
            try:
                if remaining is None:
                    self._queue.put(_STOP)
                else:
                    self._queue.put(_STOP, timeout=remaining)
            except queue.Full:
                return False
            self._stop_markers_sent += 1

        for thread in self._threads:
            remaining = self._remaining(deadline)
            thread.join(remaining)
            if thread.is_alive():
                return False

        with self._condition:
            self._state = WorkerPoolState.STOPPED
            self._condition.notify_all()
        return True

    def stats(self) -> WorkerPoolStats:
        with self._condition:
            return WorkerPoolStats(
                state=self._state,
                submitted=self._submitted,
                completed=self._completed,
                failed=self._failed,
                rejected_full=self._rejected_full,
                rejected_not_running=self._rejected_not_running,
                discarded_on_stop=self._discarded_on_stop,
                queue_size=self._queue.qsize(),
                queue_capacity=self.config.queue_size,
                in_flight=self._in_flight,
                live_workers=self._live_workers,
                last_error=self._last_error,
            )

    def _worker_loop(self) -> None:
        processor: Processor | None = None
        initialization_error: BaseException | None = None

        try:
            if self._processor_factory is not None:
                processor = self._processor_factory()
                if not callable(processor):
                    raise TypeError("processor_factory must return a callable")
            else:
                processor = self._processor
        except BaseException as exc:  # keep worker alive to account for tasks
            initialization_error = exc
            logger.exception("ANPR worker processor initialization failed")

        with self._condition:
            self._live_workers += 1
            self._condition.notify_all()

        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    self._queue.task_done()
                    return

                task = item
                with self._condition:
                    self._in_flight += 1
                    self._condition.notify_all()

                try:
                    if self.config.manage_django_connections:
                        close_old_connections()
                    if initialization_error is not None:
                        raise initialization_error
                    if processor is None:
                        raise RuntimeError("worker processor is unavailable")

                    result = processor(task)  # type: ignore[arg-type]
                    self._record_completed()
                    self._safe_success_callback(task, result)  # type: ignore[arg-type]
                except BaseException as exc:
                    self._record_failed(exc)
                    self._safe_error_callback(task, exc)  # type: ignore[arg-type]
                finally:
                    if self.config.manage_django_connections:
                        close_old_connections()
                    self._queue.task_done()
                    with self._condition:
                        self._in_flight -= 1
                        self._condition.notify_all()
        finally:
            with self._condition:
                self._live_workers -= 1
                self._condition.notify_all()

    def _record_completed(self) -> None:
        with self._condition:
            self._completed += 1

    def _record_failed(self, exc: BaseException) -> None:
        with self._condition:
            self._failed += 1
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _safe_success_callback(self, task: TaskType, result: Any) -> None:
        if self._on_success is None:
            return
        try:
            self._on_success(task, result)
        except Exception:
            logger.exception("ANPR worker success callback failed")

    def _safe_error_callback(
        self,
        task: TaskType,
        exc: BaseException,
    ) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(task, exc)
        except Exception:
            logger.exception("ANPR worker error callback failed")

    def _discard_queued_tasks(self) -> None:
        discarded = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break

            self._queue.task_done()
            if item is not _STOP:
                discarded += 1

        with self._condition:
            self._discarded_on_stop += discarded
            self._condition.notify_all()

    def _is_idle_unlocked(self) -> bool:
        return self._queue.empty() and self._in_flight == 0

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())