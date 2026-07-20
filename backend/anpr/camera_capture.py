"""
Continuous, non-blocking camera capture service.

The capture thread continuously reads from OpenCV and publishes
FramePacket objects into a bounded drop-oldest queue. AI processing,
database work, and frontend requests never run inside this thread.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

import cv2
import numpy as np

from anpr.frame_queue import (
    DropOldestQueue,
    FramePacket,
    FrameQueueStats,
)


logger = logging.getLogger(__name__)

CameraSource = Union[int, str]
CaptureFactory = Callable[[CameraSource], Any]


@dataclass(frozen=True, slots=True)
class CameraCaptureStats:
    running: bool
    opened: bool
    ended: bool

    frames_read: int
    frames_enqueued: int
    open_failures: int
    read_failures: int
    reconnects: int

    source_fps: float
    last_error: Optional[str]

    queue: FrameQueueStats


class CameraCaptureService:
    """
    Continuously capture frames in a dedicated thread.

    Live sources reconnect after failures. Uploaded video files stop
    cleanly at end-of-file. Only frames matching target_fps enter the
    bounded frame queue, while the camera itself continues being read.
    """

    def __init__(
        self,
        *,
        source: CameraSource,
        gate_id: int,
        target_fps: int = 10,
        queue_size: int = 30,
        source_name: str = "",
        replay_video_in_real_time: bool = False,
        reconnect_delay: float = 2.0,
        capture_factory: CaptureFactory = cv2.VideoCapture,
    ):
        if gate_id <= 0:
            raise ValueError(
                "gate_id must be greater than zero."
            )

        if target_fps <= 0:
            raise ValueError(
                "target_fps must be greater than zero."
            )

        if target_fps > 60:
            raise ValueError(
                "target_fps cannot exceed 60."
            )

        if queue_size <= 0:
            raise ValueError(
                "queue_size must be greater than zero."
            )

        if reconnect_delay < 0:
            raise ValueError(
                "reconnect_delay cannot be negative."
            )

        self.source = source
        self.gate_id = gate_id
        self.target_fps = target_fps
        self.source_name = (
            source_name.strip()
            if source_name
            else "camera"
        )

        self.replay_video_in_real_time = (
            replay_video_in_real_time
        )
        self.reconnect_delay = reconnect_delay
        self.capture_factory = capture_factory

        self.frame_queue = DropOldestQueue[
            FramePacket
        ](
            maxsize=queue_size
        )

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()

        self._state_lock = threading.Lock()
        self._capture_lock = threading.Lock()

        self._thread: Optional[threading.Thread] = None
        self._capture = None

        self._running = False
        self._opened = False
        self._ended = False

        self._sequence = 0
        self._frames_read = 0
        self._frames_enqueued = 0
        self._open_failures = 0
        self._read_failures = 0
        self._reconnects = 0

        self._source_fps = 0.0
        self._last_error: Optional[str] = None

    def start(self):
        if (
            self._thread is not None
            and self._thread.is_alive()
        ):
            return self

        if self._thread is not None:
            raise RuntimeError(
                "CameraCaptureService instances "
                "cannot be restarted."
            )

        self._stop_event.clear()
        self._ready_event.clear()

        with self._state_lock:
            self._running = True
            self._opened = False
            self._ended = False
            self._last_error = None

        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"camera-capture-gate-{self.gate_id}",
            daemon=True,
        )

        self._thread.start()

        return self

    def stop(
        self,
        *,
        timeout: float = 5.0,
        clear_queue: bool = False,
    ) -> bool:
        """
        Request shutdown and wait for the capture thread.

        Returns True when the thread has stopped.
        """

        self._stop_event.set()

        # Releasing the OpenCV object helps unblock a pending read()
        # for disconnected network cameras.
        self._release_capture()

        thread = self._thread

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(0.0, timeout))

        if clear_queue:
            self.frame_queue.clear()

        return not self.is_alive()

    def is_alive(self) -> bool:
        return bool(
            self._thread is not None
            and self._thread.is_alive()
        )

    def wait_until_ready(
        self,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        Wait until the source has opened successfully at least once.
        """

        return self._ready_event.wait(timeout=timeout)

    def get_frame(
        self,
        timeout: Optional[float] = None,
    ) -> FramePacket:
        return self.frame_queue.get(timeout=timeout)

    def task_done(self) -> None:
        self.frame_queue.task_done()

    def stats(self) -> CameraCaptureStats:
        with self._state_lock:
            return CameraCaptureStats(
                running=self._running,
                opened=self._opened,
                ended=self._ended,
                frames_read=self._frames_read,
                frames_enqueued=self._frames_enqueued,
                open_failures=self._open_failures,
                read_failures=self._read_failures,
                reconnects=self._reconnects,
                source_fps=self._source_fps,
                last_error=self._last_error,
                queue=self.frame_queue.stats(),
            )

    def _capture_loop(self) -> None:
        has_opened_once = False
        frame_period = 1.0 / self.target_fps
        next_enqueue_at = 0.0

        try:
            while not self._stop_event.is_set():
                capture = self._open_capture()

                if capture is None:
                    if self._stop_event.wait(
                        self.reconnect_delay
                    ):
                        break

                    continue

                source_fps = self._read_source_fps(capture)

                with self._state_lock:
                    self._opened = True
                    self._source_fps = source_fps
                    self._last_error = None

                    if has_opened_once:
                        self._reconnects += 1

                has_opened_once = True
                self._ready_event.set()
                next_enqueue_at = 0.0

                source_frame_delay = (
                    1.0 / source_fps
                    if source_fps > 0
                    else frame_period
                )

                while not self._stop_event.is_set():
                    read_started_at = time.monotonic()

                    try:
                        success, frame = capture.read()
                    except Exception as error:
                        logger.exception(
                            "Camera frame read raised an error"
                        )

                        self._mark_read_failure(
                            f"Frame read error: {error}"
                        )
                        break

                    if (
                        not success
                        or frame is None
                        or not isinstance(frame, np.ndarray)
                        or frame.size == 0
                    ):
                        self._mark_read_failure(
                            "Camera returned an empty frame."
                        )
                        break

                    with self._state_lock:
                        self._frames_read += 1

                    if self.replay_video_in_real_time:
                        elapsed = (
                            time.monotonic()
                            - read_started_at
                        )

                        remaining = (
                            source_frame_delay - elapsed
                        )

                        if (
                            remaining > 0
                            and self._stop_event.wait(
                                remaining
                            )
                        ):
                            break

                    captured_monotonic = time.monotonic()

                    if captured_monotonic < next_enqueue_at:
                        continue

                    with self._state_lock:
                        self._sequence += 1
                        sequence = self._sequence

                    packet = FramePacket(
                        sequence=sequence,
                        gate_id=self.gate_id,
                        frame=frame,
                        source_name=self.source_name,
                        captured_monotonic=(
                            captured_monotonic
                        ),
                    )

                    self.frame_queue.put_latest(packet)

                    with self._state_lock:
                        self._frames_enqueued += 1

                    next_enqueue_at = (
                        captured_monotonic + frame_period
                    )

                self._release_capture()

                with self._state_lock:
                    self._opened = False

                if self._stop_event.is_set():
                    break

                if self.replay_video_in_real_time:
                    with self._state_lock:
                        self._ended = True
                    break

                if self._stop_event.wait(
                    self.reconnect_delay
                ):
                    break

        finally:
            self._release_capture()

            with self._state_lock:
                self._running = False
                self._opened = False

    def _open_capture(self):
        try:
            capture = self.capture_factory(self.source)
        except Exception as error:
            logger.exception(
                "Opening camera source raised an error"
            )

            self._mark_open_failure(
                f"Camera open error: {error}"
            )
            return None

        try:
            opened = bool(capture.isOpened())
        except Exception as error:
            self._safe_release(capture)
            self._mark_open_failure(
                f"Could not check camera state: {error}"
            )
            return None

        if not opened:
            self._safe_release(capture)
            self._mark_open_failure(
                "Could not open camera source."
            )
            return None

        try:
            capture.set(
                cv2.CAP_PROP_BUFFERSIZE,
                1,
            )
        except Exception:
            # Some OpenCV backends do not support buffer sizing.
            logger.debug(
                "Camera backend does not support buffer sizing",
                exc_info=True,
            )

        with self._capture_lock:
            self._capture = capture

        return capture

    def _read_source_fps(self, capture) -> float:
        try:
            fps = float(
                capture.get(cv2.CAP_PROP_FPS)
            )
        except Exception:
            return 0.0

        if fps <= 0 or fps > 240:
            return 0.0

        return fps

    def _mark_open_failure(self, message: str) -> None:
        with self._state_lock:
            self._opened = False
            self._open_failures += 1
            self._last_error = message

    def _mark_read_failure(self, message: str) -> None:
        with self._state_lock:
            self._read_failures += 1
            self._last_error = message

    def _release_capture(self) -> None:
        with self._capture_lock:
            capture = self._capture
            self._capture = None

        self._safe_release(capture)

    @staticmethod
    def _safe_release(capture) -> None:
        if capture is None:
            return

        try:
            capture.release()
        except Exception:
            logger.debug(
                "Camera release failed",
                exc_info=True,
            )