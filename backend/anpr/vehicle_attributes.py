"""
Vehicle attribute detection utilities for the Campus ANPR system.

This module is deliberately separate from ``detector.py`` (which owns
number-plate detection + OCR and is left untouched by this change). It
adds a second, independent detection pass over the *whole* vehicle in
frame: body type, dominant colour, and (optionally) make/model.

Nothing here talks to Django models -- every function takes/returns
plain numpy arrays, tuples and dicts, exactly like detector.py, so it
can be called directly from views.py or unit-tested in isolation.

Public functions
-----------------
    detect_vehicle(image)               -> (bbox, vehicle_type, type_confidence)
    crop_vehicle(image, bbox)           -> np.ndarray | None
    detect_vehicle_color(vehicle_crop)  -> (color, color_confidence)
    predict_vehicle_make_model(crop)    -> {"company", "model", "confidence"}
    split_make_model_class(class_name)  -> (company, model)

CPU performance
----------------
* The general vehicle-detection YOLO model and the make/model
  classifier (if present) are each loaded exactly once per process and
  cached -- see ``get_vehicle_yolo_model`` / ``get_make_model_classifier``.
* Callers are expected to only invoke this module *after* a plate has
  already been detected on the frame (see anpr/views.py) so this extra
  inference cost is paid only on frames that already contain a
  candidate vehicle, not on every 2.5s webcam tick.
* The make/model classifier is intentionally optional: if the model
  file configured via ``ANPR_MAKE_MODEL_PATH`` does not exist, or
  loading/inference fails for any reason, ``predict_vehicle_make_model``
  always degrades to Unknown/Unknown/0.0 rather than raising -- the
  ANPR endpoint must keep working without it.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Cached, lazily-loaded models
# ---------------------------------------------------------------------

_vehicle_yolo_model = None
_vehicle_yolo_lock = threading.Lock()

_make_model_classifier = None
_make_model_classes: Optional[dict] = None
_make_model_unavailable = False
_make_model_lock = threading.Lock()

# COCO class ids the general-purpose YOLO model already knows, mapped
# onto the four vehicle types this project exposes. Anything else
# (person, bicycle, traffic light, ...) is ignored.
VEHICLE_TYPE_LABELS = {
    2: "Car",
    3: "Motorcycle",
    5: "Bus",
    7: "Truck",
}

MAKE_MODEL_CONFIDENCE_THRESHOLD = 0.60
VEHICLE_DETECTION_CONFIDENCE_THRESHOLD = 0.35
COLOR_CONFIDENCE_THRESHOLD = 0.35


def get_vehicle_yolo_model():
    """
    Load a general-purpose, COCO-pretrained YOLO model exactly once.

    This model is only used to locate *the vehicle* in the frame (car
    vs motorcycle vs bus vs truck) -- it has nothing to do with plate
    detection, which stays entirely inside detector.py.
    """

    global _vehicle_yolo_model

    if _vehicle_yolo_model is not None:
        return _vehicle_yolo_model

    with _vehicle_yolo_lock:
        if _vehicle_yolo_model is not None:
            return _vehicle_yolo_model

        from django.conf import settings
        from ultralytics import YOLO

        # A small, generic pretrained checkpoint (yolov8n.pt) is the
        # sane default -- it already knows car/motorcycle/bus/truck
        # out of the box and is cheap enough to run on CPU alongside
        # the plate model. Override via settings if you have a
        # campus-specific vehicle detector.
        model_path = getattr(
            settings,
            "ANPR_VEHICLE_YOLO_MODEL_PATH",
            "yolov8n.pt",
        )

        logger.info(
            "Loading general vehicle YOLO model from %s",
            model_path,
        )

        _vehicle_yolo_model = YOLO(str(model_path))

    return _vehicle_yolo_model


def get_make_model_classifier():
    """
    Load the optional make/model classifier exactly once.

    Returns ``(model, class_names)``, or ``(None, None)`` when no
    classifier is configured, the file does not exist, or it fails to
    load. Never raises -- callers must treat ``None`` as "run without
    make/model classification".
    """

    global _make_model_classifier, _make_model_classes, _make_model_unavailable

    if _make_model_classifier is not None:
        return _make_model_classifier, _make_model_classes

    if _make_model_unavailable:
        return None, None

    with _make_model_lock:
        if _make_model_classifier is not None:
            return _make_model_classifier, _make_model_classes

        if _make_model_unavailable:
            return None, None

        from django.conf import settings

        default_path = Path(__file__).resolve().parent / "models" / "vehicle_make_model.pt"
        model_path = Path(getattr(settings, "ANPR_MAKE_MODEL_PATH", default_path))

        if not model_path.exists():
            logger.info(
                "Make/model classifier not found at %s -- "
                "company/model detection is disabled.",
                model_path,
            )
            _make_model_unavailable = True
            return None, None

        try:
            # Expected to be a YOLOv8 classification checkpoint
            # (yolov8*-cls), trained on classes such as "tata_nexon",
            # "hyundai_creta", etc. Ultralytics' YOLO() loader infers
            # the task (classify) from the checkpoint itself.
            from ultralytics import YOLO

            classifier = YOLO(str(model_path))
            class_names = classifier.names

        except Exception:
            logger.exception(
                "Failed to load make/model classifier from %s -- "
                "company/model detection is disabled.",
                model_path,
            )
            _make_model_unavailable = True
            return None, None

        _make_model_classifier = classifier
        _make_model_classes = class_names

    return _make_model_classifier, _make_model_classes


# ---------------------------------------------------------------------
# Vehicle detection + crop
# ---------------------------------------------------------------------

def detect_vehicle(
    image: np.ndarray,
    confidence_threshold: float = VEHICLE_DETECTION_CONFIDENCE_THRESHOLD,
):
    """
    Detect the single most likely vehicle in a full camera frame.

    Returns:
        (bbox, vehicle_type, type_confidence)

        ``bbox`` is ``[x1, y1, x2, y2]`` in pixel coordinates, or
        ``None`` when no vehicle-shaped object was found above
        threshold. ``vehicle_type`` is one of "Car", "Motorcycle",
        "Bus", "Truck", or "Unknown" when nothing was found.
    """

    if image is None or image.size == 0:
        return None, "Unknown", 0.0

    try:
        model = get_vehicle_yolo_model()

        predictions = list(
            model.predict(
                source=image,
                conf=confidence_threshold,
                classes=list(VEHICLE_TYPE_LABELS.keys()),
                imgsz=640,
                iou=0.45,
                max_det=10,
                verbose=False,
                device="cpu",
            )
        )
    except Exception:
        logger.exception("General vehicle detection failed")
        return None, "Unknown", 0.0

    image_height, image_width = image.shape[:2]

    best_box = None
    best_type = "Unknown"
    best_confidence = 0.0
    best_area = 0

    for prediction in predictions:
        boxes = prediction.boxes

        if boxes is None:
            continue

        for box in boxes:
            confidence = float(box.conf[0].detach().cpu().item())
            class_id = int(box.cls[0].detach().cpu().item())

            if class_id not in VEHICLE_TYPE_LABELS or confidence < confidence_threshold:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0].detach().cpu().tolist())
            x1 = max(0, min(x1, image_width - 1))
            y1 = max(0, min(y1, image_height - 1))
            x2 = max(0, min(x2, image_width))
            y2 = max(0, min(y2, image_height))

            width = x2 - x1
            height = y2 - y1

            if width <= 0 or height <= 0:
                continue

            area = width * height

            # The vehicle being scanned at the gate is almost always
            # the largest confident vehicle in frame, not one passing
            # in the background -- prefer largest area among
            # detections that clear the confidence threshold.
            if area > best_area:
                best_area = area
                best_box = [x1, y1, x2, y2]
                best_type = VEHICLE_TYPE_LABELS[class_id]
                best_confidence = confidence

    if best_box is None:
        return None, "Unknown", 0.0

    return best_box, best_type, round(best_confidence, 3)


def crop_vehicle(
    image: Optional[np.ndarray],
    bbox,
    margin_ratio: float = 0.03,
) -> Optional[np.ndarray]:
    """
    Crop the vehicle out of the full frame, with a small margin.

    Returns ``None`` when the image or bbox is missing/invalid so
    callers can cheaply skip color/make-model work.
    """

    if image is None or bbox is None:
        return None

    x1, y1, x2, y2 = bbox
    height, width = image.shape[:2]

    box_w = x2 - x1
    box_h = y2 - y1

    if box_w <= 0 or box_h <= 0:
        return None

    margin_x = int(box_w * margin_ratio)
    margin_y = int(box_h * margin_ratio)

    x1 = max(0, x1 - margin_x)
    y1 = max(0, y1 - margin_y)
    x2 = min(width, x2 + margin_x)
    y2 = min(height, y2 + margin_y)

    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2].copy()


# ---------------------------------------------------------------------
# Color detection
# ---------------------------------------------------------------------

def detect_vehicle_color(vehicle_crop: Optional[np.ndarray]):
    """
    Estimate the dominant *body* colour of a cropped vehicle image.

    Approach (see module docstring / spec):
    * Works only on the vehicle crop, never the full frame.
    * Samples a central "body band" of the crop -- skipping the top
      (roof/windshield/sky), the bottom (bumper/plate/road shadow) and
      the outer edges (background bleeding into a loose YOLO box) --
      as a lightweight stand-in for real background/window
      segmentation.
    * Classifies in HSV space into a fixed palette, and reports
      "Unknown" whenever the sampled region doesn't clearly agree on
      one bucket, rather than guessing.
    """

    if vehicle_crop is None or vehicle_crop.size == 0:
        return "Unknown", 0.0

    crop = cv2.resize(vehicle_crop, (160, 160), interpolation=cv2.INTER_AREA)
    height, width = crop.shape[:2]

    top = int(height * 0.30)
    bottom = int(height * 0.85)
    left = int(width * 0.12)
    right = int(width * 0.88)

    body_region = crop[top:bottom, left:right]

    if body_region.size == 0:
        return "Unknown", 0.0

    hsv = cv2.cvtColor(body_region, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)

    hue = hsv[:, 0]
    saturation = hsv[:, 1]
    value = hsv[:, 2]

    mean_sat = float(np.mean(saturation))
    mean_val = float(np.mean(value))

    if mean_val < 55:
        color = "Black"
        matching = float(np.mean(value < 70))
    elif mean_sat < 30:
        if mean_val > 190:
            color = "White"
            matching = float(np.mean((saturation < 35) & (value > 170)))
        elif mean_val > 120:
            color = "Silver"
            matching = float(np.mean((saturation < 40) & (value > 100) & (value <= 190)))
        else:
            color = "Gray"
            matching = float(np.mean((saturation < 40) & (value <= 120)))
    else:
        # Chromatic body colour: bucket by hue (OpenCV hue range 0-179).
        hue_buckets = [
            ("Red", 0, 8),
            ("Orange", 8, 20),
            ("Yellow", 20, 33),
            ("Green", 33, 85),
            ("Blue", 85, 130),
            ("Red", 155, 180),  # red wraps around the hue circle
        ]

        color = "Brown"
        matching = 0.0

        for label, low, high in hue_buckets:
            in_bucket = float(np.mean((hue >= low) & (hue < high)))
            if in_bucket > matching:
                matching = in_bucket
                color = label

        # A dark, low-value orange/yellow reads as brown paint rather
        # than a bright orange/yellow one.
        if color in ("Orange", "Yellow") and mean_val < 130:
            color = "Brown"

    confidence = round(min(max(matching, 0.0), 1.0), 3)

    if confidence < COLOR_CONFIDENCE_THRESHOLD:
        return "Unknown", confidence

    return color, confidence


# ---------------------------------------------------------------------
# Make / model classification (optional)
# ---------------------------------------------------------------------

def predict_vehicle_make_model(vehicle_crop: Optional[np.ndarray]) -> dict:
    """
    Predict company + model using a dedicated trained classifier.

    Always returns ``{"company", "model", "confidence"}`` -- never
    raises. Falls back to Unknown/Unknown/0.0 whenever the classifier
    is missing, fails to load, fails to run, or isn't confident enough
    (see ``MAKE_MODEL_CONFIDENCE_THRESHOLD``).
    """

    unknown_result = {"company": "Unknown", "model": "Unknown", "confidence": 0.0}

    if vehicle_crop is None or vehicle_crop.size == 0:
        return unknown_result

    classifier, class_names = get_make_model_classifier()

    if classifier is None:
        return unknown_result

    try:
        resized = cv2.resize(vehicle_crop, (224, 224), interpolation=cv2.INTER_AREA)

        predictions = classifier.predict(
            source=resized,
            verbose=False,
            device="cpu",
        )

        if not predictions:
            return unknown_result

        probs = predictions[0].probs

        if probs is None:
            return unknown_result

        top_index = int(probs.top1)
        confidence = float(probs.top1conf)

        class_name = class_names.get(top_index) if class_names else None

        if not class_name or confidence < MAKE_MODEL_CONFIDENCE_THRESHOLD:
            return unknown_result

        company, model_name = split_make_model_class(class_name)

        return {
            "company": company,
            "model": model_name,
            "confidence": round(confidence, 3),
        }

    except Exception:
        logger.exception("Make/model prediction failed")
        return unknown_result


def split_make_model_class(class_name: str):
    """
    Split a classifier class name such as ``"tata_nexon"`` into
    ``("Tata", "Nexon")``.

    Falls back to using the whole string as the company with an
    "Unknown" model when no separator is present.
    """

    if not class_name:
        return "Unknown", "Unknown"

    parts = class_name.replace("-", "_").split("_", 1)

    company = parts[0].strip().title() or "Unknown"

    if len(parts) > 1 and parts[1].strip():
        model_name = parts[1].strip().replace("_", " ").title()
    else:
        model_name = "Unknown"

    return company, model_name