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
_text_recognizer = None
_conflict_text_recognizer = None

_model_load_lock = threading.Lock()
_ocr_load_lock = threading.Lock()
_text_recognizer_load_lock = threading.Lock()
_conflict_recognizer_load_lock = threading.Lock()

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

# A crop at or above this width/height ratio is treated as a single text
# line (a typical car plate) and is routed to PaddleOCR's recognition-only
# module. Crops below this ratio are treated as possibly containing two
# stacked lines (motorcycle/scooter plates) and keep the full detector +
# recognizer pipeline. preprocess_plate_crop() and run_ocr() must agree on
# this threshold: the border preprocess_plate_crop() adds is deliberately
# smaller for shapes on the single-line side of this threshold (see
# preprocess_plate_crop docstring), so both functions read it from the same
# constant instead of each hard-coding "2.2".
SINGLE_LINE_ASPECT_RATIO_THRESHOLD = 2.2

# A valid identity conflict is resolved only when the full PaddleOCR pipeline
# disagrees by exactly one character and supplies unusually strong evidence.
# Otherwise the observation is rejected rather than risking a false record.
FULL_PIPELINE_CONFLICT_MIN_CONFIDENCE = 0.90
FULL_PIPELINE_CONFLICT_MIN_ADVANTAGE = 0.08


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


def get_text_recognizer():
    """Load PaddleOCR's recognition-only model for single-line plates."""

    global _text_recognizer

    if _text_recognizer is not None:
        return _text_recognizer

    with _text_recognizer_load_lock:
        if _text_recognizer is not None:
            return _text_recognizer

        from paddleocr import TextRecognition

        logger.info("Loading PaddleOCR text recognizer")
        _text_recognizer = TextRecognition(
            model_name="en_PP-OCRv5_mobile_rec",
            device="cpu",
            engine="paddle_static",
        )

    return _text_recognizer


def get_conflict_text_recognizer():
    """Load the stronger PaddleOCR model used only for OCR conflicts."""

    global _conflict_text_recognizer

    if _conflict_text_recognizer is not None:
        return _conflict_text_recognizer

    with _conflict_recognizer_load_lock:
        if _conflict_text_recognizer is not None:
            return _conflict_text_recognizer

        from paddleocr import TextRecognition

        logger.info("Loading PaddleOCR conflict text recognizer")
        _conflict_text_recognizer = TextRecognition(
            model_name="PP-OCRv6_medium_rec",
            device="cpu",
            engine="paddle_static",
        )

    return _conflict_text_recognizer


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


def _trim_single_line_plate_context(
    gray: np.ndarray,
) -> tuple[np.ndarray, tuple[int, int, int, int] | None]:
    """Trim dark vehicle context around a bright single-line plate.

    The YOLO crop remains the authority. This helper only trims when a large,
    wide, bright rectangle occupies the expected central plate region. If the
    evidence is weak, the original image is returned unchanged.
    """

    height, width = gray.shape[:2]
    if width < 24 or height < 8:
        return gray, None

    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bright_mask = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    bright_mask = cv2.morphologyEx(
        bright_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        bright_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    image_area = float(width * height)
    candidates: list[tuple[float, int, int, int, int]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width <= 0 or box_height <= 0:
            continue
        aspect_ratio = box_width / float(box_height)
        coverage = (box_width * box_height) / image_area
        center_x = x + box_width / 2.0
        center_y = y + box_height / 2.0
        centrally_located = (
            width * 0.20 <= center_x <= width * 0.80
            and height * 0.15 <= center_y <= height * 0.85
        )
        if (
            2.0 <= aspect_ratio <= 9.0
            and box_width >= width * 0.50
            and box_height >= height * 0.20
            and coverage >= 0.15
            and centrally_located
        ):
            candidates.append(
                (
                    coverage * aspect_ratio,
                    x,
                    y,
                    box_width,
                    box_height,
                )
            )

    if not candidates:
        return gray, None

    _, x, y, box_width, box_height = max(candidates)
    horizontal_margin = max(1, int(round(box_width * 0.02)))
    vertical_margin = max(1, int(round(box_height * 0.08)))
    x1 = max(0, x - horizontal_margin)
    y1 = max(0, y - vertical_margin)
    x2 = min(width, x + box_width + horizontal_margin)
    y2 = min(height, y + box_height + vertical_margin)
    trimmed = gray[y1:y2, x1:x2]
    if trimmed.size == 0:
        return gray, None
    return np.ascontiguousarray(trimmed), (x1, y1, x2, y2)


def preprocess_plate_crop(
    crop: np.ndarray,
) -> np.ndarray:
    """
    Prepare a plate crop for PaddleOCR without destroying tiny characters.

    Processing:
    - grayscale
    - upscale before filtering so small character strokes are preserved
    - gentle bilateral denoising
    - local contrast enhancement and mild sharpening
    - a neutral margin to help PaddleOCR separate text from the plate border

    The crop's raw (pre-upscale) width/height ratio is also computed and
    logged here as `single_line` / `multi_line`, using the same
    SINGLE_LINE_ASPECT_RATIO_THRESHOLD that run_ocr() uses to route the
    crop to PaddleOCR's recognition-only module vs. the full
    detector+recognizer pipeline. This is diagnostic only in this revision:
    an earlier attempt to also shrink the margin for single-line shapes was
    tested against video3.mp4 and made recognition worse (it additionally
    collapsed an adjacent repeated character on top of the pre-existing
    trailing-character loss), so margin sizing was reverted to the single,
    original value below pending a better-supported hypothesis.
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

    height, width = gray.shape[:2]

    if width <= 0 or height <= 0:
        raise ValueError(
            "The plate crop has invalid dimensions."
        )

    # Captured before any resizing/bordering below. This is the only point
    # where the crop's true, unmodified proportions are available, so the
    # single-line/two-line shape classification is decided here and reused
    # for the margin size below.
    raw_aspect_ratio = float(width) / float(height)
    is_single_line_shape = (
        raw_aspect_ratio >= SINGLE_LINE_ASPECT_RATIO_THRESHOLD
    )

    trim_box = None
    if is_single_line_shape:
        gray, trim_box = _trim_single_line_plate_context(gray)
        height, width = gray.shape[:2]

    # A typical CCTV plate crop can be only 10-20 pixels high. Applying a
    # large filter at that resolution merges neighbouring character strokes.
    # Enlarge first and keep the aspect ratio. The upper bound prevents a
    # malformed one-pixel crop from creating an excessively large image.
    scale = max(
        1.0,
        320.0 / float(width),
        64.0 / float(height),
    )
    scale = min(scale, 8.0)

    if scale > 1.0:
        resized_width = max(
            1,
            int(round(width * scale)),
        )
        resized_height = max(
            1,
            int(round(height * scale)),
        )

        gray = cv2.resize(
            gray,
            (resized_width, resized_height),
            interpolation=cv2.INTER_CUBIC,
        )

    denoised = cv2.bilateralFilter(
        gray,
        5,
        35,
        35,
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )
    enhanced = clahe.apply(denoised)

    blurred = cv2.GaussianBlur(
        enhanced,
        (0, 0),
        sigmaX=1.0,
        sigmaY=1.0,
    )
    sharpened = cv2.addWeighted(
        enhanced,
        1.35,
        blurred,
        -0.35,
        0,
    )

    output_height, output_width = sharpened.shape[:2]

    # NOTE: an earlier iteration of this function shrank the margin for
    # single-line shapes on the theory that PaddleOCR's recognition-only
    # module was losing canvas width to whitespace. A real run against
    # video3.mp4 showed that change made recognition worse (it additionally
    # collapsed an adjacent repeated character, "MM" -> "M", on top of the
    # pre-existing trailing-character loss), so it was reverted. The
    # single, larger margin below is the original, evidence-supported
    # value for both shapes. is_single_line_shape/raw_aspect_ratio are
    # still computed above and logged below so we have real data on the
    # crop's native proportions for the next hypothesis, without changing
    # OCR behaviour again until that data says to.
    vertical_margin = max(
        4,
        int(round(output_height * 0.12)),
    )
    horizontal_margin = max(
        8,
        int(round(output_width * 0.04)),
    )

    bordered = cv2.copyMakeBorder(
        sharpened,
        vertical_margin,
        vertical_margin,
        horizontal_margin,
        horizontal_margin,
        borderType=cv2.BORDER_CONSTANT,
        value=255,
    )

    logger.debug(
        "preprocess_plate_crop: raw=%sx%s ratio=%.2f shape=%s "
        "trim=%r processed_source=%sx%s scale=%.2f "
        "bordered=%sx%s margins=(v%s,h%s)",
        crop.shape[1],
        crop.shape[0],
        raw_aspect_ratio,
        "single_line" if is_single_line_shape else "multi_line",
        trim_box,
        width,
        height,
        scale,
        bordered.shape[1],
        bordered.shape[0],
        vertical_margin,
        horizontal_margin,
    )

    return bordered


def _collect_text_recognition_results(
    results,
) -> list[tuple[str, float]]:
    """Extract every ``rec_text`` and ``rec_score`` PaddleX result."""

    readings: list[tuple[str, float]] = []
    if not results:
        return readings

    for result in results:
        try:
            text = str(result["rec_text"]).strip()
            score = float(result["rec_score"])
        except (KeyError, TypeError, AttributeError, ValueError):
            payload = getattr(result, "json", {})
            if callable(payload):
                payload = payload()
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if isinstance(payload, dict):
                payload = payload.get("res", payload)
                text = str(payload.get("rec_text") or "").strip()
                try:
                    score = float(payload.get("rec_score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
            else:
                text = ""
                score = 0.0

        if text:
            readings.append((text, score))

    return readings


def _read_text_recognition_results(
    results,
) -> tuple[str, float]:
    """Backward-compatible extraction of the first PaddleX result."""

    readings = _collect_text_recognition_results(results)
    if readings:
        return readings[0]

    return "", 0.0


def _normalized_valid_plate(raw_text: str) -> str:
    """Return a strict normalized plate, or an empty string when invalid."""

    validation = validate_and_normalize_plate(
        raw_text,
        max_corrections=1,
        allow_edge_noise=False,
    )
    if not validation.is_valid:
        return ""
    return validation.normalized_text


def _is_adjacent_repeat_restoration(
    shorter: str,
    longer: str,
) -> bool:
    """Check whether ``longer`` restores one adjacent repeated character.

    CTC-style sequence recognizers can collapse a real repeated character
    (for example ``MM`` to ``M``). This helper is deliberately generic: it
    does not assume a particular letter, plate, state, or series position.
    """

    if len(longer) != len(shorter) + 1:
        return False

    for index in range(len(longer)):
        without_character = longer[:index] + longer[index + 1 :]
        if without_character != shorter:
            continue

        repeats_left = (
            index > 0
            and longer[index] == longer[index - 1]
        )
        repeats_right = (
            index + 1 < len(longer)
            and longer[index] == longer[index + 1]
        )
        if repeats_left or repeats_right:
            return True

    return False


def _is_single_edit_apart(first: str, second: str) -> bool:
    """Return whether two non-identical strings differ by one edit."""

    if first == second or abs(len(first) - len(second)) > 1:
        return False

    if len(first) == len(second):
        differences = sum(
            left != right
            for left, right in zip(first, second)
        )
        return differences == 1

    shorter, longer = (
        (first, second)
        if len(first) < len(second)
        else (second, first)
    )
    return any(
        longer[:index] + longer[index + 1 :] == shorter
        for index in range(len(longer))
    )


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

    # YOLO has already isolated the plate. Standard wide plates contain one
    # text line, so sending them through another text detector can crop or
    # reorder tiny characters. Use PaddleOCR's recognition-only module for
    # those images. Narrow/tall crops retain the full OCR pipeline because
    # motorcycle and scooter plates can contain two text lines. This must
    # use the same threshold preprocess_plate_crop() used to size the
    # border, or a crop could be preprocessed for one path and then routed
    # to the other.
    height, width = ocr_input.shape[:2]
    input_ratio = width / max(1, height)
    direct_text = ""
    direct_confidence = 0.0
    direct_variant_votes = 0
    if input_ratio >= SINGLE_LINE_ASPECT_RATIO_THRESHOLD:
        recognizer = get_text_recognizer()
        recognition_inputs = [ocr_input]
        for horizontal_scale in (1.20, 1.35):
            recognition_inputs.append(
                cv2.resize(
                    ocr_input,
                    (
                        max(1, int(round(width * horizontal_scale))),
                        height,
                    ),
                    interpolation=cv2.INTER_CUBIC,
                )
            )
        with _ocr_inference_lock:
            recognition_results = recognizer.predict(
                input=recognition_inputs,
                batch_size=len(recognition_inputs),
            )
        direct_readings = _collect_text_recognition_results(
            recognition_results
        )
        valid_groups: dict[str, list[tuple[str, float]]] = {}
        for raw_text, confidence in direct_readings:
            validation = validate_and_normalize_plate(
                raw_text,
                max_corrections=1,
                allow_edge_noise=False,
            )
            if validation.is_valid:
                valid_groups.setdefault(
                    validation.normalized_text,
                    [],
                ).append((raw_text, confidence))

        if valid_groups:
            _, winning_group = max(
                valid_groups.items(),
                key=lambda item: (
                    len(item[1]),
                    sum(value[1] for value in item[1]) / len(item[1]),
                    max(value[1] for value in item[1]),
                ),
            )
            direct_variant_votes = len(winning_group)
            direct_text, direct_confidence = max(
                winning_group,
                key=lambda value: value[1],
            )
        elif direct_readings:
            direct_text, direct_confidence = max(
                direct_readings,
                key=lambda value: value[1],
            )

        logger.debug(
            "run_ocr: path=recognition_only crop=%sx%s ratio=%.2f "
            "readings=%r winner=%r confidence=%.3f variant_votes=%s",
            width,
            height,
            input_ratio,
            direct_readings,
            direct_text,
            direct_confidence,
            direct_variant_votes,
        )
        if direct_text:
            logger.debug(
                "run_ocr: comparing recognition-only result with the "
                "independent full pipeline (raw_text=%r, "
                "variant_votes=%s)",
                direct_text,
                direct_variant_votes,
            )

        else:
            logger.debug(
                "run_ocr: recognition_only returned no text "
                "(crop=%sx%s); falling back to full detector pipeline",
                width,
                height,
            )

    reader = get_ocr_reader()

    with _ocr_inference_lock:
        ocr_results = reader.predict(
            input=ocr_input,
        )

    if not ocr_results:
        logger.debug(
            "run_ocr: path=full_pipeline crop=%sx%s ratio=%.2f "
            "returned no OCR results",
            width,
            height,
            input_ratio,
        )
        return direct_text, direct_confidence

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
        logger.debug(
            "run_ocr: path=full_pipeline crop=%sx%s ratio=%.2f "
            "found text regions but no non-empty text",
            width,
            height,
            input_ratio,
        )
        return direct_text, direct_confidence

    combined_text = "".join(text_parts)

    average_confidence = (
        sum(confidence_values)
        / len(confidence_values)
        if confidence_values
        else 0.0
    )

    logger.debug(
        "run_ocr: path=full_pipeline crop=%sx%s ratio=%.2f "
        "raw_text=%r confidence=%.3f parts=%r",
        width,
        height,
        input_ratio,
        combined_text,
        average_confidence,
        text_parts,
    )

    direct_normalized = _normalized_valid_plate(direct_text)
    full_normalized = _normalized_valid_plate(combined_text)

    if direct_normalized and full_normalized:
        if direct_normalized == full_normalized:
            if direct_confidence >= average_confidence:
                return direct_text, direct_confidence
            return combined_text, average_confidence

        if _is_adjacent_repeat_restoration(
            direct_normalized,
            full_normalized,
        ):
            logger.info(
                "PaddleOCR full pipeline restored an adjacent repeated "
                "character: %s -> %s",
                direct_normalized,
                full_normalized,
            )
            return combined_text, average_confidence

        if _is_adjacent_repeat_restoration(
            full_normalized,
            direct_normalized,
        ) and direct_variant_votes >= 2:
            logger.info(
                "PaddleOCR recognition ensemble restored an adjacent "
                "repeated character: %s -> %s",
                full_normalized,
                direct_normalized,
            )
            return direct_text, direct_confidence

        full_confidence_advantage = (
            average_confidence - direct_confidence
        )
        if (
            _is_single_edit_apart(
                direct_normalized,
                full_normalized,
            )
            and average_confidence
            >= FULL_PIPELINE_CONFLICT_MIN_CONFIDENCE
            and full_confidence_advantage
            >= FULL_PIPELINE_CONFLICT_MIN_ADVANTAGE
        ):
            logger.info(
                "PaddleOCR full pipeline resolved a one-character "
                "identity conflict: %s (%.3f) -> %s (%.3f)",
                direct_normalized,
                direct_confidence,
                full_normalized,
                average_confidence,
            )
            return combined_text, average_confidence

        # Both routes produced structurally valid but different plates. A
        # confidence score cannot resolve an identity conflict safely.
        logger.warning(
            "PaddleOCR identity conflict; rejecting observation "
            "(recognition=%s %.3f, full=%s %.3f)",
            direct_normalized,
            direct_confidence,
            full_normalized,
            average_confidence,
        )
        return "", 0.0

    if full_normalized:
        return combined_text, average_confidence

    if direct_normalized and direct_variant_votes >= 2:
        return direct_text, direct_confidence

    # Neither route supplied a trustworthy consensus. Preserve the most
    # confident raw observation for diagnostics; strict plate validation in
    # the processor will prevent an invalid value from being saved.
    if direct_text and direct_confidence >= average_confidence:
        return direct_text, direct_confidence
    return combined_text, average_confidence


def run_conflict_ocr(
    raw_plate_crop: np.ndarray,
) -> tuple[str, float]:
    """Run high-accuracy PaddleOCR on one unprocessed ambiguity crop.

    This path is intentionally separate from ``run_ocr``. It is designed for
    the rare case where multiple mobile-model observations are individually
    valid but disagree on the plate identity. The caller remains responsible
    for strict plate validation and for ensuring this result corroborates an
    identity already observed on the same Track ID.
    """

    if raw_plate_crop is None or raw_plate_crop.size == 0:
        return "", 0.0

    if raw_plate_crop.ndim == 2:
        gray = raw_plate_crop.copy()
    elif (
        raw_plate_crop.ndim == 3
        and raw_plate_crop.shape[2] == 4
    ):
        gray = cv2.cvtColor(
            raw_plate_crop,
            cv2.COLOR_BGRA2GRAY,
        )
    else:
        gray = cv2.cvtColor(
            raw_plate_crop,
            cv2.COLOR_BGR2GRAY,
        )

    height, width = gray.shape[:2]
    single_line = (
        width / max(1, height)
        >= SINGLE_LINE_ASPECT_RATIO_THRESHOLD
    )
    if single_line:
        trimmed, _ = _trim_single_line_plate_context(gray)
        cubic = cv2.resize(
            trimmed,
            None,
            fx=4.0,
            fy=4.0,
            interpolation=cv2.INTER_CUBIC,
        )
        lanczos = cv2.resize(
            trimmed,
            None,
            fx=4.0,
            fy=4.0,
            interpolation=cv2.INTER_LANCZOS4,
        )
        otsu = cv2.threshold(
            cubic,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1]
        fixed_100 = cv2.threshold(
            cubic,
            100,
            255,
            cv2.THRESH_BINARY,
        )[1]
        fixed_120 = cv2.threshold(
            cubic,
            120,
            255,
            cv2.THRESH_BINARY,
        )[1]
        grayscale_variants = [
            cubic,
            lanczos,
            otsu,
            fixed_100,
            fixed_120,
        ]
        ocr_inputs = [
            cv2.cvtColor(
                cv2.copyMakeBorder(
                    variant,
                    12,
                    12,
                    16,
                    16,
                    cv2.BORDER_CONSTANT,
                    value=255,
                ),
                cv2.COLOR_GRAY2BGR,
            )
            for variant in grayscale_variants
        ]
        # This function supplies one frame-level vote. Final authorization is
        # deliberately not decided here: VehicleProcessor requires the same
        # identity from at least two different retained frames. Keeping the
        # best valid variant allows partial evidence from a difficult frame
        # to participate in that stronger cross-frame consensus.
        required_variant_votes = 1
    else:
        ocr_inputs = [cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)]
        required_variant_votes = 1

    recognizer = get_conflict_text_recognizer()
    with _ocr_inference_lock:
        results = recognizer.predict(
            input=ocr_inputs,
            batch_size=len(ocr_inputs),
        )

    readings = _collect_text_recognition_results(results)
    if not readings:
        logger.warning("PaddleOCR conflict recognizer returned no text")
        return "", 0.0

    valid_groups: dict[str, list[tuple[str, float]]] = {}
    for raw_text, confidence in readings:
        validation = validate_and_normalize_plate(
            raw_text,
            max_corrections=1,
            allow_edge_noise=False,
        )
        if validation.is_valid:
            valid_groups.setdefault(
                validation.normalized_text,
                [],
            ).append((raw_text, confidence))

    if not valid_groups:
        raw_text, confidence = max(
            readings,
            key=lambda value: value[1],
        )
        logger.warning(
            "PaddleOCR conflict ensemble found no valid plate: %r",
            readings,
        )
        return raw_text, confidence

    normalized, winning_group = max(
        valid_groups.items(),
        key=lambda item: (
            len(item[1]),
            sum(value[1] for value in item[1]) / len(item[1]),
            max(value[1] for value in item[1]),
        ),
    )
    if len(winning_group) < required_variant_votes:
        logger.warning(
            "PaddleOCR conflict ensemble rejected unstable readings: %r",
            {
                plate: values
                for plate, values in sorted(valid_groups.items())
            },
        )
        return "", 0.0

    raw_text, _ = max(
        winning_group,
        key=lambda value: value[1],
    )
    confidence = sum(
        value[1] for value in winning_group
    ) / len(winning_group)
    logger.warning(
        "PaddleOCR conflict consensus: normalized=%s raw=%r "
        "confidence=%.3f votes=%s/%s crop=%sx%s readings=%r",
        normalized,
        raw_text,
        confidence,
        len(winning_group),
        len(ocr_inputs),
        width,
        height,
        readings,
    )
    return raw_text, confidence


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
