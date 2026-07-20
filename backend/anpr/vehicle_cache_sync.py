"""Lifecycle service that keeps the camera process vehicle cache current."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from django.utils import timezone

from anpr.vehicle_cache import VehicleCache, get_vehicle_cache


CacheLoader = Callable[[], int]


@dataclass(frozen=True, slots=True)
class VehicleCacheSyncConfig:
    refresh_interval_seconds: float = 30.0
    retry_interval_seconds: float = 5.0
    thread_name: str = "anpr-vehicle-cache-refresh"

    def __post_init__(self) -> None:
        if self.refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be greater than 0")
        if self.retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be greater than 0")
        if not self.thread_name.strip():
            raise ValueError("thread_name cannot be empty")


@dataclass(frozen=True, slots=True)
class VehicleCacheSyncStats:
    running: bool
    refresh_attempts: int
    refresh_successes: int
    refresh_failures: int
    last_vehicle_count: int
    last_started_at: datetime | None
    last_completed_at: datetime | None
    last_error: str


class VehicleCacheRefreshService:
    """Warm and periodically refresh one process-local VehicleCache.

    Refresh work runs outside the camera and vehicle worker threads.  A failed
    refresh never replaces the last known-good cache because ``VehicleCache``
    performs an atomic copy-on-write swap.
    """

    def __init__(
        self,
        *,
        cache: VehicleCache | None = None,
        config: VehicleCacheSyncConfig | None = None,
        loader: CacheLoader | None = None,
    ) -> None:
        self.cache = cache or get_vehicle_cache()
        self.config = config or VehicleCacheSyncConfig()
        self._loader = loader or self.cache.refresh
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._starting = False
        self._refresh_attempts = 0
        self._refresh_successes = 0
        self._refresh_failures = 0
        self._last_vehicle_count = 0
        self._last_started_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._last_error = ""

    def start(self, *, warm: bool = True) -> bool:
        """Warm synchronously, then start periodic background refreshes."""

        with self._state_lock:
            if self._running or self._starting:
                return False
            self._starting = True

        try:
            if warm:
                # Fail closed: camera startup should not silently classify all
                # registered plates as unknown when the initial DB load fails.
                self.refresh_now()
        except Exception:
            with self._state_lock:
                self._starting = False
            raise

        with self._state_lock:
            if not self._starting:
                # A concurrent stop request cancelled startup while the warm
                # refresh was in progress.
                return False
            self._stop_event.clear()
            self._running = True
            self._starting = False
            self._thread = threading.Thread(
                target=self._run,
                name=self.config.thread_name,
                daemon=True,
            )
            self._thread.start()
            return True

    def refresh_now(self) -> int:
        """Perform one serialized refresh in the caller's thread."""

        with self._refresh_lock:
            started_at = timezone.now()
            with self._state_lock:
                self._refresh_attempts += 1
                self._last_started_at = started_at

            try:
                count = int(self._loader())
                if count < 0:
                    raise ValueError("cache loader returned a negative count")
            except Exception as exc:
                with self._state_lock:
                    self._refresh_failures += 1
                    self._last_completed_at = timezone.now()
                    self._last_error = f"{type(exc).__name__}: {exc}"
                raise

            with self._state_lock:
                self._refresh_successes += 1
                self._last_vehicle_count = count
                self._last_completed_at = timezone.now()
                self._last_error = ""
            return count

    def stop(self, timeout: float | None = 5.0) -> bool:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout cannot be negative")

        with self._state_lock:
            thread = self._thread
            if not self._running and not self._starting:
                return True
            self._stop_event.set()

        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                return False

        with self._state_lock:
            self._running = False
            self._starting = False
            self._thread = None
            return True

    def stats(self) -> VehicleCacheSyncStats:
        with self._state_lock:
            return VehicleCacheSyncStats(
                running=self._running,
                refresh_attempts=self._refresh_attempts,
                refresh_successes=self._refresh_successes,
                refresh_failures=self._refresh_failures,
                last_vehicle_count=self._last_vehicle_count,
                last_started_at=self._last_started_at,
                last_completed_at=self._last_completed_at,
                last_error=self._last_error,
            )

    def _run(self) -> None:
        delay = self.config.refresh_interval_seconds
        try:
            while not self._stop_event.wait(delay):
                try:
                    self.refresh_now()
                    delay = self.config.refresh_interval_seconds
                except Exception:
                    delay = self.config.retry_interval_seconds
        finally:
            with self._state_lock:
                self._running = False
                