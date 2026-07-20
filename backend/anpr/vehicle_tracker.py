"""Vehicle detection and tracking for the continuous ANPR pipeline.

This module intentionally does not perform plate detection or OCR.  One
``VehicleTracker`` instance belongs to one camera pipeline so ByteTrack state
cannot leak between gates.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from django.conf import settings


VEHICLE_CLASS_NAMES = frozenset(
    {
        "bicycle",
        "car",
        "motorcycle",
        "motorbike",
        "scooter",
        "bus",
        "truck",
    }
)

CLASS_NAME_ALIASES = {
    "motorbike": "motorcycle",
    "scooter": "motorcycle",
}


class VehicleTrackerError(RuntimeError):
    """Raised when the vehicle tracker cannot load or process a frame."""


@dataclass(frozen=True, slots=True)
class VehicleDetection:
    """One vehicle reported by YOLO tracking for the current frame."""

    track_id: int | None
    class_id: int
    vehicle_type: str
    confidence: float
    bbox: tuple[int, int, int, int]

    @property
    def is_tracked(self) -> bool:
        return self.track_id is not None

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class VehicleTrackingResult:
    """All supported vehicles found in a single frame."""

    detections: tuple[VehicleDetection, ...]
    frame_width: int
    frame_height: int
    inference_ms: float

    @property
    def vehicle_count(self) -> int:
        return len(self.detections)

    @property
    def tracked_count(self) -> int:
        return sum(item.is_tracked for item in self.detections)


@dataclass(frozen=True, slots=True)
class VehicleTrackerStats:
    frames_processed: int
    vehicles_detected: int
    failures: int
    last_inference_ms: float


@dataclass(frozen=True, slots=True)
class VehicleTrackerConfig:
    confidence: float = 0.35
    iou: float = 0.50
    tracker: str = "bytetrack.yaml"
    image_size: int = 640
    device: str | int | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError("confidence must be greater than 0 and at most 1")
        if not 0.0 < self.iou <= 1.0:
            raise ValueError("iou must be greater than 0 and at most 1")
        if self.image_size < 320:
            raise ValueError("image_size must be at least 320")
        if not str(self.tracker).strip():
            raise ValueError("tracker configuration cannot be empty")


class VehicleTracker:
    """Stateful YOLO tracker owned by exactly one camera/gate pipeline.

    Tracking calls must remain sequential for a camera.  Expensive plate OCR
    work will later run in a separate worker pool; it must not call this class.
    """

    def __init__(
        self,
        *,
        model_source: str | Path | None = None,
        config: VehicleTrackerConfig | None = None,
        model: Any | None = None,
    ) -> None:
        self.config = config or VehicleTrackerConfig(
            confidence=float(
                getattr(settings, "ANPR_VEHICLE_CONFIDENCE", 0.35)
            ),
            iou=float(getattr(settings, "ANPR_VEHICLE_IOU", 0.50)),
            tracker=str(
                getattr(settings, "ANPR_TRACKER_CONFIG", "bytetrack.yaml")
            ),
            image_size=int(
                getattr(settings, "ANPR_VEHICLE_IMAGE_SIZE", 640)
            ),
            device=getattr(settings, "ANPR_YOLO_DEVICE", None),
        )

        self.model_source = Path(
            model_source
            or getattr(
                settings,
                "ANPR_VEHICLE_MODEL_PATH",
                Path(settings.BASE_DIR)
                / "anpr"
                / "models"
                / "yolov8n.pt",
            )
        )

        self._model = model
        self._class_names: dict[int, str] = {}
        self._vehicle_class_ids: tuple[int, ...] = ()
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._frames_processed = 0
        self._vehicles_detected = 0
        self._failures = 0
        self._last_inference_ms = 0.0

        if model is not None:
            self._configure_classes(model)

    @property
    def vehicle_class_ids(self) -> tuple[int, ...]:
        self._ensure_model()
        return self._vehicle_class_ids

    @property
    def class_names(self) -> Mapping[int, str]:
        self._ensure_model()
        return dict(self._class_names)

    def track(self, frame: np.ndarray) -> VehicleTrackingResult:
        """Track every supported vehicle in one BGR frame."""

        self._validate_frame(frame)
        model = self._ensure_model()
        started = time.perf_counter()

        kwargs: dict[str, Any] = {
            "source": frame,
            "persist": True,
            "tracker": self.config.tracker,
            "conf": self.config.confidence,
            "iou": self.config.iou,
            "imgsz": self.config.image_size,
            "classes": list(self._vehicle_class_ids),
            "verbose": False,
        }
        if self.config.device not in (None, ""):
            kwargs["device"] = self.config.device

        try:
            # A tracker is stateful.  Never allow concurrent frames to overtake
            # one another, even if a caller accidentally uses multiple threads.
            with self._inference_lock:
                raw_results = model.track(**kwargs)

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            detections = self._parse_results(raw_results, frame.shape)
            result = VehicleTrackingResult(
                detections=tuple(detections),
                frame_width=int(frame.shape[1]),
                frame_height=int(frame.shape[0]),
                inference_ms=elapsed_ms,
            )
            self._record_success(len(detections), elapsed_ms)
            return result
        except VehicleTrackerError:
            self._record_failure()
            raise
        except Exception as exc:
            self._record_failure()
            raise VehicleTrackerError(
                f"YOLO vehicle tracking failed: {exc}"
            ) from exc

    def stats(self) -> VehicleTrackerStats:
        with self._stats_lock:
            return VehicleTrackerStats(
                frames_processed=self._frames_processed,
                vehicles_detected=self._vehicles_detected,
                failures=self._failures,
                last_inference_ms=self._last_inference_ms,
            )

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            if not self.model_source.is_file():
                raise VehicleTrackerError(
                    "Vehicle model was not found at "
                    f"'{self.model_source}'. Keep yolov8n.pt separate from "
                    "the license-plate best.pt model."
                )

            try:
                from ultralytics import YOLO

                model = YOLO(str(self.model_source))
                self._configure_classes(model)
                self._model = model
            except VehicleTrackerError:
                raise
            except Exception as exc:
                raise VehicleTrackerError(
                    f"Unable to load vehicle model '{self.model_source}': {exc}"
                ) from exc

        return self._model

    def _configure_classes(self, model: Any) -> None:
        raw_names = getattr(model, "names", None)
        if raw_names is None and hasattr(model, "model"):
            raw_names = getattr(model.model, "names", None)

        names = self._normalise_names(raw_names)
        selected = tuple(
            class_id
            for class_id, class_name in names.items()
            if class_name.lower().strip() in VEHICLE_CLASS_NAMES
        )

        if not selected:
            raise VehicleTrackerError(
                "The configured model has no supported vehicle classes. "
                "Expected bicycle, car, motorcycle, bus, or truck."
            )

        self._class_names = names
        self._vehicle_class_ids = selected

    @staticmethod
    def _normalise_names(raw_names: Any) -> dict[int, str]:
        if isinstance(raw_names, Mapping):
            return {
                int(class_id): str(class_name)
                for class_id, class_name in raw_names.items()
            }
        if isinstance(raw_names, Sequence) and not isinstance(
            raw_names, (str, bytes)
        ):
            return {
                class_id: str(class_name)
                for class_id, class_name in enumerate(raw_names)
            }
        raise VehicleTrackerError("The vehicle model has invalid class names.")

    def _parse_results(
        self,
        raw_results: Any,
        frame_shape: tuple[int, ...],
    ) -> list[VehicleDetection]:
        if raw_results is None:
            return []

        results = list(raw_results)
        if not results:
            return []

        boxes = getattr(results[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        xyxy = self._to_numpy(getattr(boxes, "xyxy", None))
        confidences = self._to_numpy(getattr(boxes, "conf", None))
        class_ids = self._to_numpy(getattr(boxes, "cls", None))
        raw_track_ids = getattr(boxes, "id", None)
        track_ids = (
            self._to_numpy(raw_track_ids)
            if raw_track_ids is not None
            else None
        )

        if xyxy is None or confidences is None or class_ids is None:
            raise VehicleTrackerError("YOLO returned incomplete box data.")

        height, width = int(frame_shape[0]), int(frame_shape[1])
        detections: list[VehicleDetection] = []

        for index in range(len(xyxy)):
            class_id = int(class_ids[index])
            if class_id not in self._vehicle_class_ids:
                continue

            bbox = self._clamp_box(xyxy[index], width, height)
            if bbox is None:
                continue

            track_id: int | None = None
            if track_ids is not None and index < len(track_ids):
                candidate = float(track_ids[index])
                if np.isfinite(candidate) and candidate >= 0:
                    track_id = int(candidate)

            raw_name = self._class_names[class_id].lower().strip()
            vehicle_type = CLASS_NAME_ALIASES.get(raw_name, raw_name)
            detections.append(
                VehicleDetection(
                    track_id=track_id,
                    class_id=class_id,
                    vehicle_type=vehicle_type,
                    confidence=float(confidences[index]),
                    bbox=bbox,
                )
            )

        return detections

    @staticmethod
    def _to_numpy(value: Any) -> np.ndarray | None:
        if value is None:
            return None
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "numpy"):
            value = value.numpy()
        return np.asarray(value)

    @staticmethod
    def _clamp_box(
        coordinates: Any,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int, int, int] | None:
        values = np.asarray(coordinates, dtype=float).reshape(-1)
        if len(values) < 4 or not np.all(np.isfinite(values[:4])):
            return None

        x1 = max(0, min(frame_width, int(round(values[0]))))
        y1 = max(0, min(frame_height, int(round(values[1]))))
        x2 = max(0, min(frame_width, int(round(values[2]))))
        y2 = max(0, min(frame_height, int(round(values[3]))))
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    @staticmethod
    def _validate_frame(frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be a NumPy array")
        if frame.ndim not in (2, 3) or frame.size == 0:
            raise ValueError("frame must be a non-empty image")
        if frame.shape[0] < 2 or frame.shape[1] < 2:
            raise ValueError("frame dimensions are too small")

    def _record_success(self, count: int, elapsed_ms: float) -> None:
        with self._stats_lock:
            self._frames_processed += 1
            self._vehicles_detected += count
            self._last_inference_ms = elapsed_ms

    def _record_failure(self) -> None:
        with self._stats_lock:
            self._failures += 1
