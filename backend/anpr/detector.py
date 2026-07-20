"""
Core ANPR detection engine.

Current compatible pipeline:
    Image
    -> YOLOv8 licence-plate detection
    -> select best plate for the existing API
    -> OpenCV preprocessing
    -> PaddleOCR
    -> plate normalization
    -> registered-vehicle lookup

The detector can now return all plate bounding boxes internally. The existing
run_full_pipeline() interface still processes the highest-confidence plate so
the current frontend and DetectPlateView remain compatible.
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from anpr.plate_validation import (
    clean_plate_text as clean_indian_plate_text,
    validate_and_normalize_plate,
)


logger = logging.getLogger(__name__)

_yolo_model = None
_ocr_reader = None

_model_load_lock = threading.Lock()
_ocr_load_lock = threading.Lock()

# Ultralytics and PaddleOCR objects should not be used concurrently by multiple
# request threads until the later worker-process architecture is introduced.
_yolo_inference_lock = threading.Lock()
_ocr_inference_lock = threading.Lock()


@dataclass
class DetectionResult:
    success: bool

    raw_plate_text: str = ""
    cleaned_plate_text: str = ""

    confidence_score: float = 0.0
    bounding_box: Optional[list[int]] = None

    # Contains every plate candidate found in the frame. The existing API
    # continues to use bounding_box for the highest-confidence candidate.
    plate_candidates: list[dict] = field(default_factory=list)

    error: Optional[str] = None
    matched_vehicle: Optional[dict] = None
    authorization_status: str = "UNKNOWN"

    # JPEG bytes containing the cropped number plate.
    plate_crop_bytes: Optional[bytes] = None


ALLOWED_PLATE_CLASS_NAMES = {
    "license_plate",
    "number_plate",
    "licence_plate",
    "plate",
}


def _normalize_class_name(value: str) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _model_class_names(model) -> set[str]:
    names = getattr(model, "names", {})

    if isinstance(names, dict):
        values = names.values()
    elif isinstance(names, (list, tuple)):
        values = names
    else:
        values = []

    return {
        _normalize_class_name(value)
        for value in values
    }


def get_yolo_model():
    """
    Load and cache the trained licence-plate YOLO model.

    The model is loaded once for each Django process.
    """

    global _yolo_model

    if _yolo_model is not None:
        return _yolo_model

    with _model_load_lock:
        if _yolo_model is not None:
            return _yolo_model

        from django.conf import settings
        from ultralytics import YOLO

        model_path = Path(
            settings.ANPR_YOLO_MODEL_PATH
        ).expanduser().resolve()

        if not model_path.is_file():
            raise FileNotFoundError(
                f"ANPR YOLO model was not found: {model_path}"
            )

        logger.info(
            "Loading ANPR YOLO model from %s",
            model_path,
        )

        model = YOLO(str(model_path))

        class_names = _model_class_names(model)

        if not class_names.intersection(
            ALLOWED_PLATE_CLASS_NAMES
        ):
            raise RuntimeError(
                "The configured YOLO model is not a licence-plate "
                f"detector. Model classes: {sorted(class_names)}"
            )

        _yolo_model = model

    return _yolo_model


def get_ocr_reader():
    """
    Load and cache one CPU PaddleOCR pipeline per Django process.

    YOLO has already isolated the number plate, so the lightweight English
    detection and recognition models are enough here. PaddleOCR's text detector
    is intentionally retained because motorcycle and scooter plates can contain
    two text lines.
    """

    global _ocr_reader

    if _ocr_reader is not None:
        return _ocr_reader

    with _ocr_load_lock:
        if _ocr_reader is not None:
            return _ocr_reader

        from paddleocr import PaddleOCR

        logger.info("Loading PaddleOCR reader")

        _ocr_reader = PaddleOCR(
            device="cpu",
            engine="paddle_static",
            text_detection_model_name=(
                "PP-OCRv5_mobile_det"
            ),
            text_recognition_model_name=(
                "en_PP-OCRv5_mobile_rec"
            ),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    return _ocr_reader


def clean_plate_text(raw_text: str) -> str:
    """
    Normalize OCR output for database matching.

    Example:
        "ka 25-ab 1234" -> "KA25AB1234"
    """

    return clean_indian_plate_text(raw_text)


def detect_plate_bboxes(
    image: np.ndarray,
    confidence_threshold: float = 0.4,
) -> list[dict]:
    """
    Detect every licence-plate bounding box in a frame.

    Returns:
        [
            {
                "bounding_box": [x1, y1, x2, y2],
                "confidence": 0.93,
                "class_id": 0,
                "class_name": "license_plate",
            }
        ]

    Results are ordered from highest to lowest confidence.
    """

    if image is None or image.size == 0:
        return []

    model = get_yolo_model()

    with _yolo_inference_lock:
        prediction_results = model.predict(
            source=image,
            conf=confidence_threshold,
            verbose=False,
        )

    candidates = []

    for result in prediction_results:
        boxes = getattr(result, "boxes", None)

        if boxes is None:
            continue

        result_names = getattr(
            result,
            "names",
            getattr(model, "names", {}),
        )

        for box in boxes:
            confidence = float(box.conf[0])

            if confidence < confidence_threshold:
                continue

            class_id = int(box.cls[0])

            if isinstance(result_names, dict):
                class_name = result_names.get(
                    class_id,
                    str(class_id),
                )
            elif isinstance(result_names, (list, tuple)):
                if 0 <= class_id < len(result_names):
                    class_name = result_names[class_id]
                else:
                    class_name = str(class_id)
            else:
                class_name = str(class_id)

            normalized_class_name = _normalize_class_name(
                class_name
            )

            if (
                normalized_class_name
                not in ALLOWED_PLATE_CLASS_NAMES
            ):
                continue

            coordinates = box.xyxy[0].tolist()

            if len(coordinates) != 4:
                continue

            x1, y1, x2, y2 = map(
                int,
                coordinates,
            )

            if x2 <= x1 or y2 <= y1:
                continue

            candidates.append(
                {
                    "bounding_box": [
                        x1,
                        y1,
                        x2,
                        y2,
                    ],
                    "confidence": confidence,
                    "class_id": class_id,
                    "class_name": normalized_class_name,
                }
            )

    candidates.sort(
        key=lambda candidate: candidate["confidence"],
        reverse=True,
    )

    return candidates


def detect_plate_bbox(
    image: np.ndarray,
    confidence_threshold: float = 0.4,
):
    """
    Backward-compatible helper used by the existing pipeline.

    Returns only the highest-confidence plate while detect_plate_bboxes()
    retains support for every detected plate.
    """

    candidates = detect_plate_bboxes(
        image=image,
        confidence_threshold=confidence_threshold,
    )

    if not candidates:
        return None, 0.0

    best_candidate = candidates[0]

    return (
        best_candidate["bounding_box"],
        best_candidate["confidence"],
    )


def clamp_bounding_box(
    bounding_box: list[int],
    image: np.ndarray,
) -> Optional[list[int]]:
    """
    Keep a bounding box inside the image boundaries.
    """

    if (
        image is None
        or image.size == 0
        or not bounding_box
        or len(bounding_box) != 4
    ):
        return None

    image_height, image_width = image.shape[:2]

    x1, y1, x2, y2 = bounding_box

    x1 = max(0, min(int(x1), image_width))
    y1 = max(0, min(int(y1), image_height))
    x2 = max(0, min(int(x2), image_width))
    y2 = max(0, min(int(y2), image_height))

    if x2 <= x1 or y2 <= y1:
        return None

    return [x1, y1, x2, y2]


def preprocess_plate_crop(
    crop: np.ndarray,
) -> np.ndarray:
    """
    Prepare a plate crop for OCR.

    Processing:
    - grayscale
    - bilateral denoising
    - histogram equalization
    - upscale small crops
    """

    if crop is None or crop.size == 0:
        raise ValueError("The plate crop is empty.")

    if crop.ndim == 2:
        gray = crop.copy()
    elif crop.ndim == 3 and crop.shape[2] == 4:
        gray = cv2.cvtColor(
            crop,
            cv2.COLOR_BGRA2GRAY,
        )
    else:
        gray = cv2.cvtColor(
            crop,
            cv2.COLOR_BGR2GRAY,
        )

    gray = cv2.bilateralFilter(
        gray,
        11,
        17,
        17,
    )

    gray = cv2.equalizeHist(gray)

    height, width = gray.shape[:2]

    if width <= 0 or height <= 0:
        raise ValueError(
            "The plate crop has invalid dimensions."
        )

    if width < 300:
        scale = 300 / width

        gray = cv2.resize(
            gray,
            (
                max(1, int(width * scale)),
                max(1, int(height * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )

    return gray


def run_ocr(
    preprocessed_crop: np.ndarray,
):
    """
    Read text from a processed number-plate crop.

    Returns:
        combined_text, average_confidence
    """

    if (
        preprocessed_crop is None
        or preprocessed_crop.size == 0
    ):
        return "", 0.0

    # PaddleOCR accepts numpy arrays. Convert the grayscale preprocessing
    # result back to three channels for consistent model input on Windows.
    if preprocessed_crop.ndim == 2:
        ocr_input = cv2.cvtColor(
            preprocessed_crop,
            cv2.COLOR_GRAY2BGR,
        )
    elif (
        preprocessed_crop.ndim == 3
        and preprocessed_crop.shape[2] == 4
    ):
        ocr_input = cv2.cvtColor(
            preprocessed_crop,
            cv2.COLOR_BGRA2BGR,
        )
    else:
        ocr_input = preprocessed_crop

    reader = get_ocr_reader()

    with _ocr_inference_lock:
        ocr_results = reader.predict(
            input=ocr_input,
        )

    if not ocr_results:
        return "", 0.0

    text_parts = []
    confidence_values = []

    for result in ocr_results:
        # Paddle result objects support dictionary access. The JSON fallback
        # also keeps this compatible with PaddleX result implementations.
        try:
            recognized_texts = result["rec_texts"]
            recognized_scores = result["rec_scores"]
        except (KeyError, TypeError, AttributeError):
            payload = getattr(result, "json", {})

            if callable(payload):
                payload = payload()

            if isinstance(payload, str):
                payload = json.loads(payload)

            if isinstance(payload, dict):
                payload = payload.get("res", payload)
                recognized_texts = payload.get(
                    "rec_texts",
                    [],
                )
                recognized_scores = payload.get(
                    "rec_scores",
                    [],
                )
            else:
                recognized_texts = []
                recognized_scores = []

        for index, detected_text in enumerate(
            recognized_texts
        ):
            detected_text = str(detected_text).strip()

            if not detected_text:
                continue

            try:
                confidence = float(
                    recognized_scores[index]
                )
            except (IndexError, TypeError, ValueError):
                confidence = 0.0

            text_parts.append(detected_text)
            confidence_values.append(confidence)

    if not text_parts:
        return "", 0.0

    combined_text = "".join(text_parts)

    average_confidence = (
        sum(confidence_values)
        / len(confidence_values)
        if confidence_values
        else 0.0
    )

    return combined_text, average_confidence


def match_vehicle(cleaned_plate: str):
    """
    Search the registered Vehicle table.

    The database lookup will be replaced by the authorised-vehicle RAM cache
    in a later phase.
    """

    from django.utils import timezone
    from vehicles.models import Vehicle

    if not cleaned_plate:
        return (
            None,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )

    vehicle = (
        Vehicle.objects
        .filter(
            registration_number=cleaned_plate
        )
        .select_related("department")
        .first()
    )

    if vehicle is None:
        return (
            None,
            Vehicle.AuthorizationStatus.UNAUTHORIZED,
        )

    if (
        vehicle.valid_until
        and vehicle.valid_until
        < timezone.now().date()
    ):
        return (
            vehicle,
            Vehicle.AuthorizationStatus.EXPIRED,
        )

    return vehicle, vehicle.authorization_status


def build_vehicle_data(vehicle):
    """
    Convert a Vehicle instance into frontend-compatible data.
    """

    if vehicle is None:
        return None

    department_name = (
        str(vehicle.department)
        if vehicle.department
        else None
    )

    return {
        "id": vehicle.id,
        "registration_number": (
            vehicle.registration_number
        ),
        "owner_name": vehicle.owner_name,
        "owner_type": vehicle.owner_type,
        "department": department_name,
        "vehicle_type": vehicle.vehicle_type,
        "vehicle_company": vehicle.vehicle_company,
        "vehicle_model": vehicle.vehicle_model,
        "color": vehicle.color,
        "fuel_type": vehicle.fuel_type,
    }


def run_full_pipeline(
    image_bytes: bytes,
    yolo_confidence: float = 0.4,
) -> DetectionResult:
    """
    Run the existing single-result ANPR pipeline.

    All detected plate candidates are retained in plate_candidates, but only
    the highest-confidence candidate is processed by OCR in this compatibility
    phase.
    """

    if not image_bytes:
        return DetectionResult(
            success=False,
            error="No image data was provided.",
        )

    try:
        image_array = np.frombuffer(
            image_bytes,
            dtype=np.uint8,
        )

        image = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR,
        )

        if image is None:
            return DetectionResult(
                success=False,
                error="Could not decode image.",
            )

    except Exception as exc:
        logger.exception("Image decoding failed")

        return DetectionResult(
            success=False,
            error=f"Image decode error: {exc}",
        )

    try:
        candidates = detect_plate_bboxes(
            image=image,
            confidence_threshold=yolo_confidence,
        )

    except Exception as exc:
        logger.exception(
            "YOLO plate detection failed"
        )

        return DetectionResult(
            success=False,
            error=f"Plate detection error: {exc}",
        )

    if not candidates:
        return DetectionResult(
            success=False,
            error="No number plate detected in image.",
            plate_candidates=[],
        )

    best_candidate = candidates[0]

    bounding_box = clamp_bounding_box(
        best_candidate["bounding_box"],
        image,
    )

    if bounding_box is None:
        return DetectionResult(
            success=False,
            error="Invalid plate bounding box.",
            plate_candidates=candidates,
        )

    x1, y1, x2, y2 = bounding_box

    plate_crop = image[y1:y2, x1:x2]

    if plate_crop.size == 0:
        return DetectionResult(
            success=False,
            error="Invalid plate crop region.",
            bounding_box=bounding_box,
            plate_candidates=candidates,
        )

    crop_encoded, encoded_crop = cv2.imencode(
        ".jpg",
        plate_crop,
    )

    plate_crop_bytes = (
        encoded_crop.tobytes()
        if crop_encoded
        else None
    )

    try:
        preprocessed_crop = preprocess_plate_crop(
            plate_crop
        )

    except Exception as exc:
        logger.exception(
            "Plate preprocessing failed"
        )

        return DetectionResult(
            success=False,
            error=f"Plate preprocessing error: {exc}",
            bounding_box=bounding_box,
            plate_candidates=candidates,
            plate_crop_bytes=plate_crop_bytes,
        )

    try:
        raw_text, ocr_confidence = run_ocr(
            preprocessed_crop
        )

    except Exception as exc:
        logger.exception("OCR failed")

        return DetectionResult(
            success=False,
            error=f"OCR error: {exc}",
            bounding_box=bounding_box,
            plate_candidates=candidates,
            plate_crop_bytes=plate_crop_bytes,
        )

    if not raw_text:
        return DetectionResult(
            success=False,
            error=(
                "Plate located, but its text could not "
                "be read."
            ),
            bounding_box=bounding_box,
            plate_candidates=candidates,
            plate_crop_bytes=plate_crop_bytes,
        )

    validation_result = validate_and_normalize_plate(
        raw_text,
        max_corrections=1,
        allow_edge_noise=False,
    )

    if not validation_result.is_valid:
        return DetectionResult(
            success=False,
            raw_plate_text=raw_text,
            cleaned_plate_text=(
                validation_result.cleaned_text
            ),
            error=(
                "OCR text was rejected: "
                f"{validation_result.reason}"
            ),
            bounding_box=bounding_box,
            plate_candidates=candidates,
            plate_crop_bytes=plate_crop_bytes,
        )

    cleaned_text = validation_result.normalized_text

    yolo_confidence_score = float(
        best_candidate["confidence"]
    )

    correction_penalty = (
        validation_result.corrections * 0.05
    )

    adjusted_ocr_confidence = max(
        0.0,
        float(ocr_confidence) - correction_penalty,
    )

    overall_confidence = round(
        (
            yolo_confidence_score
            + adjusted_ocr_confidence
        )
        / 2,
        3,
    )

    vehicle, authorization_status = match_vehicle(
        cleaned_text
    )

    return DetectionResult(
        success=True,
        raw_plate_text=raw_text,
        cleaned_plate_text=cleaned_text,
        confidence_score=overall_confidence,
        bounding_box=bounding_box,
        plate_candidates=candidates,
        matched_vehicle=build_vehicle_data(vehicle),
        authorization_status=authorization_status,
        plate_crop_bytes=plate_crop_bytes,
    )