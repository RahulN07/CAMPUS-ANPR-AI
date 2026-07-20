"""
ANPR detection engine.

Pipeline:
    Image bytes
        -> YOLO number-plate detection
        -> Safe plate crop
        -> Multiple image enhancement variants
        -> EasyOCR on each variant
        -> Indian plate positional correction
        -> OCR candidate voting
        -> Exact vehicle database matching
        -> DetectionResult
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


logger = logging.getLogger(__name__)

_yolo_model = None
_ocr_reader = None


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

MIN_PLATE_WIDTH = 60
MIN_PLATE_HEIGHT = 15

OCR_MIN_CONFIDENCE = 0.30
OCR_REQUIRED_VARIANT_VOTES = 2

MAX_POSITIONAL_CORRECTIONS = 1
OCR_RESIZE_WIDTH = 500


# ---------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------

@dataclass
class DetectionResult:
    success: bool
    raw_plate_text: str = ""
    cleaned_plate_text: str = ""
    confidence_score: float = 0.0
    bounding_box: Optional[list] = None
    error: Optional[str] = None
    matched_vehicle: Optional[dict] = field(default=None)
    authorization_status: str = "UNKNOWN"
    plate_crop_bytes: Optional[bytes] = None


# ---------------------------------------------------------------------
# Indian plate rules
# ---------------------------------------------------------------------

PLATE_CLEAN_PATTERN = re.compile(r"[^A-Z0-9]")

# Supported examples:
# KA01AB1234
# KA2AB1234
# KA01A1234
# KA01ABC1234
INDIAN_PLATE_PATTERN = re.compile(
    r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$"
)

VALID_STATE_CODES = {
    "AN",
    "AP",
    "AR",
    "AS",
    "BR",
    "CG",
    "CH",
    "DD",
    "DL",
    "DN",
    "GA",
    "GJ",
    "HP",
    "HR",
    "JH",
    "JK",
    "KA",
    "KL",
    "LA",
    "LD",
    "MH",
    "ML",
    "MN",
    "MP",
    "MZ",
    "NL",
    "OD",
    "PB",
    "PY",
    "RJ",
    "SK",
    "TN",
    "TR",
    "TS",
    "UK",
    "UP",
    "WB",
}


# OCR frequently confuses these letters and numbers.
LETTER_TO_DIGIT = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "T": "7",
    "B": "8",
}

DIGIT_TO_LETTER = {
    "0": "O",
    "1": "I",
    "2": "Z",
    "5": "S",
    "6": "G",
    "7": "T",
    "8": "B",
}


# ---------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------

def get_yolo_model():
    """
    Load the YOLO model only once.
    """

    global _yolo_model

    if _yolo_model is None:
        from django.conf import settings
        from ultralytics import YOLO

        model_path = getattr(
            settings,
            "ANPR_YOLO_MODEL_PATH",
            None,
        )

        if not model_path:
            raise RuntimeError(
                "ANPR_YOLO_MODEL_PATH is missing in settings.py."
            )

        model_path = Path(model_path)

        if not model_path.exists():
            raise FileNotFoundError(
                f"YOLO model not found: {model_path}"
            )

        logger.info(
            "Loading ANPR YOLO model from %s",
            model_path,
        )

        _yolo_model = YOLO(str(model_path))

    return _yolo_model


def get_ocr_reader():
    """
    Load EasyOCR only once.
    """

    global _ocr_reader

    if _ocr_reader is None:
        import easyocr

        logger.info("Loading EasyOCR reader")

        _ocr_reader = easyocr.Reader(
            ["en"],
            gpu=False,
            verbose=False,
        )

    return _ocr_reader


# ---------------------------------------------------------------------
# Text cleaning and validation
# ---------------------------------------------------------------------

def clean_plate_text(text: str) -> str:
    """
    Convert OCR text to uppercase alphanumeric characters.
    """

    if not text:
        return ""

    return PLATE_CLEAN_PATTERN.sub(
        "",
        text.upper().strip(),
    )


def is_valid_indian_plate(plate: str) -> bool:
    """
    Validate the plate format and state/UT code.
    """

    if not plate:
        return False

    if not INDIAN_PLATE_PATTERN.fullmatch(plate):
        return False

    return plate[:2] in VALID_STATE_CODES


def convert_to_letters(text: str):
    """
    Convert digits into letters where letters are expected.

    Returns:
        corrected_text
        correction_count
        conversion_success
    """

    corrected = []
    corrections = 0

    for character in text:
        if character.isalpha():
            corrected.append(character)
            continue

        replacement = DIGIT_TO_LETTER.get(character)

        if replacement is None:
            return "", 0, False

        corrected.append(replacement)
        corrections += 1

    return "".join(corrected), corrections, True


def convert_to_digits(text: str):
    """
    Convert letters into digits where digits are expected.

    Returns:
        corrected_text
        correction_count
        conversion_success
    """

    corrected = []
    corrections = 0

    for character in text:
        if character.isdigit():
            corrected.append(character)
            continue

        replacement = LETTER_TO_DIGIT.get(character)

        if replacement is None:
            return "", 0, False

        corrected.append(replacement)
        corrections += 1

    return "".join(corrected), corrections, True


def normalize_indian_plate_candidate(text: str):
    """
    Normalize OCR text using expected Indian plate positions.

    Format:
        2 state letters
        1 or 2 district digits
        1 to 3 series letters
        4 registration digits

    Only a small number of positional corrections are allowed.

    Returns:
        normalized_plate
        correction_count
    """

    cleaned = clean_plate_text(text)

    if len(cleaned) < 8 or len(cleaned) > 11:
        return "", 999

    possible_candidates = []

    for district_length in (1, 2):
        for series_length in (1, 2, 3):
            expected_length = (
                2
                + district_length
                + series_length
                + 4
            )

            if len(cleaned) != expected_length:
                continue

            state_end = 2
            district_end = state_end + district_length
            series_end = district_end + series_length

            state_part = cleaned[:state_end]
            district_part = cleaned[
                state_end:district_end
            ]
            series_part = cleaned[
                district_end:series_end
            ]
            number_part = cleaned[series_end:]

            (
                corrected_state,
                state_changes,
                state_valid,
            ) = convert_to_letters(state_part)

            (
                corrected_district,
                district_changes,
                district_valid,
            ) = convert_to_digits(district_part)

            (
                corrected_series,
                series_changes,
                series_valid,
            ) = convert_to_letters(series_part)

            (
                corrected_number,
                number_changes,
                number_valid,
            ) = convert_to_digits(number_part)

            if not all(
                (
                    state_valid,
                    district_valid,
                    series_valid,
                    number_valid,
                )
            ):
                continue

            candidate = (
                corrected_state
                + corrected_district
                + corrected_series
                + corrected_number
            )

            if not is_valid_indian_plate(candidate):
                continue

            total_changes = (
                state_changes
                + district_changes
                + series_changes
                + number_changes
            )

            possible_candidates.append(
                (
                    candidate,
                    total_changes,
                )
            )

    if not possible_candidates:
        return "", 999

    possible_candidates.sort(
        key=lambda item: item[1]
    )

    best_candidate, correction_count = possible_candidates[0]

    # Prevent heavily incorrect OCR text from becoming a
    # valid-looking registration number.
    if correction_count > MAX_POSITIONAL_CORRECTIONS:
        return "", 999

    return best_candidate, correction_count


# ---------------------------------------------------------------------
# YOLO detection
# ---------------------------------------------------------------------

def detect_plate_bbox(
    image: np.ndarray,
    confidence_threshold: float = 0.4,
):
    """
    Detect the best license-plate box.

    The selection uses:
        YOLO confidence
        bounding-box area
    """

    model = get_yolo_model()

    predictions = model.predict(
        source=image,
        conf=confidence_threshold,
        verbose=False,
        device="cpu",
    )

    best_box = None
    best_confidence = 0.0
    best_selection_score = 0.0

    image_height, image_width = image.shape[:2]
    image_area = max(
        image_height * image_width,
        1,
    )

    for prediction in predictions:
        if prediction.boxes is None:
            continue

        for box in prediction.boxes:
            confidence = float(box.conf[0])

            x1, y1, x2, y2 = map(
                int,
                box.xyxy[0].tolist(),
            )

            box_width = max(0, x2 - x1)
            box_height = max(0, y2 - y1)
            box_area = box_width * box_height

            if box_area <= 0:
                continue

            area_ratio = box_area / image_area

            selection_score = (
                confidence * 0.85
                + min(area_ratio * 20, 1.0) * 0.15
            )

            if selection_score > best_selection_score:
                best_selection_score = selection_score
                best_confidence = confidence
                best_box = [
                    x1,
                    y1,
                    x2,
                    y2,
                ]

    return best_box, best_confidence


def expand_bounding_box(
    bounding_box,
    image_shape,
):
    """
    Add a small margin around the detected plate.

    This prevents edge characters from being removed.
    """

    x1, y1, x2, y2 = bounding_box

    image_height, image_width = image_shape[:2]

    plate_width = max(1, x2 - x1)
    plate_height = max(1, y2 - y1)

    horizontal_margin = int(
        plate_width * 0.06
    )

    vertical_margin = int(
        plate_height * 0.15
    )

    expanded_x1 = max(
        0,
        x1 - horizontal_margin,
    )

    expanded_y1 = max(
        0,
        y1 - vertical_margin,
    )

    expanded_x2 = min(
        image_width,
        x2 + horizontal_margin,
    )

    expanded_y2 = min(
        image_height,
        y2 + vertical_margin,
    )

    return [
        expanded_x1,
        expanded_y1,
        expanded_x2,
        expanded_y2,
    ]


# ---------------------------------------------------------------------
# Plate image processing
# ---------------------------------------------------------------------

def calculate_sharpness(
    image: np.ndarray,
) -> float:
    """
    Return the Laplacian sharpness score.

    A higher value usually indicates a sharper crop.
    """

    if image is None or image.size == 0:
        return 0.0

    if len(image.shape) == 3:
        gray = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2GRAY,
        )
    else:
        gray = image

    return float(
        cv2.Laplacian(
            gray,
            cv2.CV_64F,
        ).var()
    )


def resize_plate(
    image: np.ndarray,
    target_width: int = OCR_RESIZE_WIDTH,
):
    """
    Upscale small plate crops for OCR.
    """

    height, width = image.shape[:2]

    if width <= 0 or height <= 0:
        return image

    if width >= target_width:
        return image.copy()

    scale = target_width / width

    target_height = max(
        1,
        int(height * scale),
    )

    return cv2.resize(
        image,
        (
            target_width,
            target_height,
        ),
        interpolation=cv2.INTER_CUBIC,
    )


def create_preprocessing_variants(
    plate_crop: np.ndarray,
):
    """
    Create four OCR variants.

    Four variants are used instead of eight because the system
    is running on CPU.
    """

    resized_color = resize_plate(
        plate_crop,
        target_width=OCR_RESIZE_WIDTH,
    )

    gray = cv2.cvtColor(
        resized_color,
        cv2.COLOR_BGR2GRAY,
    )

    filtered_gray = cv2.bilateralFilter(
        gray,
        9,
        75,
        75,
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.5,
        tileGridSize=(8, 8),
    )

    clahe_image = clahe.apply(
        filtered_gray
    )

    sharpening_kernel = np.array(
        [
            [0, -1, 0],
            [-1, 5, -1],
            [0, -1, 0],
        ],
        dtype=np.float32,
    )

    sharpened = cv2.filter2D(
        clahe_image,
        -1,
        sharpening_kernel,
    )

    blurred = cv2.GaussianBlur(
        sharpened,
        (0, 0),
        1.0,
    )

    unsharp = cv2.addWeighted(
        sharpened,
        1.8,
        blurred,
        -0.8,
        0,
    )

    _, otsu_threshold = cv2.threshold(
        clahe_image,
        0,
        255,
        cv2.THRESH_BINARY
        + cv2.THRESH_OTSU,
    )

    return [
        (
            "color",
            resized_color,
        ),
        (
            "clahe",
            clahe_image,
        ),
        (
            "unsharp",
            unsharp,
        ),
        (
            "otsu",
            otsu_threshold,
        ),
    ]


def encode_plate_crop(
    plate_crop: np.ndarray,
):
    """
    Encode the original plate crop as high-quality JPEG bytes.
    """

    success, encoded_crop = cv2.imencode(
        ".jpg",
        plate_crop,
        [
            cv2.IMWRITE_JPEG_QUALITY,
            95,
        ],
    )

    if not success:
        return None

    return encoded_crop.tobytes()


# ---------------------------------------------------------------------
# EasyOCR
# ---------------------------------------------------------------------

def sort_ocr_results(
    ocr_results,
):
    """
    Sort OCR boxes from left to right.
    """

    def left_position(result):
        bounding_box = result[0]

        return min(
            point[0]
            for point in bounding_box
        )

    return sorted(
        ocr_results,
        key=left_position,
    )


def read_variant_with_easyocr(
    image: np.ndarray,
):
    """
    Run EasyOCR on one image variant.

    Returns:
        combined_text
        average_confidence
    """

    reader = get_ocr_reader()

    ocr_results = reader.readtext(
        image,
        detail=1,
        paragraph=False,
        decoder="greedy",
        allowlist=(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
        ),
        text_threshold=0.30,
        low_text=0.15,
        link_threshold=0.20,
        mag_ratio=1.0,
    )

    if not ocr_results:
        return "", 0.0

    ordered_results = sort_ocr_results(
        ocr_results
    )

    text_parts = []
    confidence_values = []

    for _, detected_text, confidence in ordered_results:
        cleaned_part = clean_plate_text(
            detected_text
        )

        if not cleaned_part:
            continue

        text_parts.append(cleaned_part)
        confidence_values.append(
            float(confidence)
        )

    if not text_parts:
        return "", 0.0

    combined_text = "".join(text_parts)

    average_confidence = (
        sum(confidence_values)
        / len(confidence_values)
    )

    return combined_text, average_confidence


def select_best_ocr_candidate(
    plate_crop: np.ndarray,
):
    """
    OCR each preprocessing variant and select the best candidate.

    A candidate must:
        have a valid Indian plate structure
        use a valid state code
        require no more than one positional correction
        appear in at least two preprocessing variants
    """

    variants = create_preprocessing_variants(
        plate_crop
    )

    candidate_data = defaultdict(
        lambda: {
            "count": 0,
            "confidence_sum": 0.0,
            "best_confidence": 0.0,
            "raw_texts": [],
            "corrections": 999,
            "variants": [],
        }
    )

    all_raw_results = []

    for variant_name, variant_image in variants:
        try:
            raw_text, confidence = (
                read_variant_with_easyocr(
                    variant_image
                )
            )

        except Exception:
            logger.exception(
                "EasyOCR failed for variant %s",
                variant_name,
            )
            continue

        if not raw_text:
            continue

        cleaned_raw = clean_plate_text(
            raw_text
        )

        all_raw_results.append(
            (
                variant_name,
                cleaned_raw,
                confidence,
            )
        )

        if confidence < OCR_MIN_CONFIDENCE:
            continue

        (
            normalized_candidate,
            correction_count,
        ) = normalize_indian_plate_candidate(
            cleaned_raw
        )

        if not normalized_candidate:
            continue

        information = candidate_data[
            normalized_candidate
        ]

        information["count"] += 1
        information["confidence_sum"] += confidence

        information["best_confidence"] = max(
            information["best_confidence"],
            confidence,
        )

        information["raw_texts"].append(
            cleaned_raw
        )

        information["variants"].append(
            variant_name
        )

        information["corrections"] = min(
            information["corrections"],
            correction_count,
        )

    if not candidate_data:
        raw_fallback = ""

        if all_raw_results:
            all_raw_results.sort(
                key=lambda item: item[2],
                reverse=True,
            )

            raw_fallback = all_raw_results[0][1]

        return {
            "success": False,
            "raw_text": raw_fallback,
            "plate": "",
            "confidence": 0.0,
            "votes": 0,
            "debug_results": all_raw_results,
        }

    best_plate = ""
    best_score = -1.0
    best_information = None

    for candidate, information in candidate_data.items():
        average_confidence = (
            information["confidence_sum"]
            / information["count"]
        )

        vote_score = min(
            information["count"] / 4,
            1.0,
        )

        correction_penalty = (
            information["corrections"] * 0.10
        )

        candidate_score = (
            average_confidence * 0.55
            + vote_score * 0.45
            - correction_penalty
        )

        if candidate_score > best_score:
            best_score = candidate_score
            best_plate = candidate
            best_information = information

    if best_information is None:
        return {
            "success": False,
            "raw_text": "",
            "plate": "",
            "confidence": 0.0,
            "votes": 0,
            "debug_results": all_raw_results,
        }

    average_confidence = (
        best_information["confidence_sum"]
        / best_information["count"]
    )

    raw_text = (
        best_information["raw_texts"][0]
        if best_information["raw_texts"]
        else best_plate
    )

    # At least two enhanced versions must recognize the same plate.
    if (
        best_information["count"]
        < OCR_REQUIRED_VARIANT_VOTES
    ):
        return {
            "success": False,
            "raw_text": raw_text,
            "plate": "",
            "confidence": average_confidence,
            "votes": best_information["count"],
            "debug_results": all_raw_results,
        }

    return {
        "success": True,
        "raw_text": raw_text,
        "plate": best_plate,
        "confidence": average_confidence,
        "votes": best_information["count"],
        "corrections": best_information["corrections"],
        "variants": best_information["variants"],
        "debug_results": all_raw_results,
    }


# ---------------------------------------------------------------------
# Vehicle database matching
# ---------------------------------------------------------------------

def match_vehicle_exact(
    cleaned_plate: str,
):
    """
    Authorize only by exact database matching.

    Fuzzy matching is intentionally not used for automatic
    authorization.
    """

    from django.utils import timezone
    from vehicles.models import Vehicle

    vehicle = (
        Vehicle.objects
        .filter(
            registration_number__iexact=cleaned_plate
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

    return (
        vehicle,
        vehicle.authorization_status,
    )


def build_vehicle_data(vehicle):
    """
    Convert the matched vehicle model into response data.
    """

    if vehicle is None:
        return None

    department_value = None

    if getattr(vehicle, "department", None):
        department_value = str(
            vehicle.department
        )

    return {
        "id": vehicle.id,
        "registration_number": getattr(
            vehicle,
            "registration_number",
            "",
        ),
        "owner_name": getattr(
            vehicle,
            "owner_name",
            "",
        ),
        "owner_type": getattr(
            vehicle,
            "owner_type",
            "",
        ),
        "department": department_value,
        "vehicle_type": getattr(
            vehicle,
            "vehicle_type",
            "",
        ),
        "vehicle_company": getattr(
            vehicle,
            "vehicle_company",
            "",
        ),
        "vehicle_model": getattr(
            vehicle,
            "vehicle_model",
            "",
        ),
        "color": getattr(
            vehicle,
            "color",
            "",
        ),
        "fuel_type": getattr(
            vehicle,
            "fuel_type",
            "",
        ),
    }


# ---------------------------------------------------------------------
# Complete pipeline
# ---------------------------------------------------------------------

def run_full_pipeline(
    image_bytes: bytes,
    yolo_confidence: float = 0.4,
) -> DetectionResult:
    """
    Complete plate detection and OCR pipeline.
    """

    # Decode image.
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
                error="Could not decode the image.",
            )

    except Exception as error:
        logger.exception(
            "Image decoding failed"
        )

        return DetectionResult(
            success=False,
            error=f"Image decoding error: {error}",
        )

    # Detect plate.
    try:
        (
            bounding_box,
            detection_confidence,
        ) = detect_plate_bbox(
            image,
            confidence_threshold=yolo_confidence,
        )

    except Exception as error:
        logger.exception(
            "YOLO plate detection failed"
        )

        return DetectionResult(
            success=False,
            error=f"Plate detection error: {error}",
        )

    if bounding_box is None:
        return DetectionResult(
            success=False,
            error="No number plate detected.",
        )

    expanded_box = expand_bounding_box(
        bounding_box,
        image.shape,
    )

    x1, y1, x2, y2 = expanded_box

    plate_crop = image[
        y1:y2,
        x1:x2,
    ]

    if plate_crop.size == 0:
        return DetectionResult(
            success=False,
            bounding_box=expanded_box,
            error="The detected plate crop is empty.",
        )

    plate_crop_bytes = encode_plate_crop(
        plate_crop
    )

    crop_height, crop_width = plate_crop.shape[:2]

    # Reject crops that contain insufficient original detail.
    if (
        crop_width < MIN_PLATE_WIDTH
        or crop_height < MIN_PLATE_HEIGHT
    ):
        return DetectionResult(
            success=False,
            confidence_score=round(
                detection_confidence,
                3,
            ),
            bounding_box=expanded_box,
            plate_crop_bytes=plate_crop_bytes,
            error=(
                "Number plate detected, but it is too small "
                f"for reliable OCR: "
                f"{crop_width}x{crop_height}px."
            ),
        )

    crop_sharpness = calculate_sharpness(
        plate_crop
    )

    # Run multi-pass OCR.
    try:
        ocr_result = select_best_ocr_candidate(
            plate_crop
        )

    except Exception as error:
        logger.exception(
            "Multi-pass EasyOCR failed"
        )

        return DetectionResult(
            success=False,
            confidence_score=round(
                detection_confidence,
                3,
            ),
            bounding_box=expanded_box,
            plate_crop_bytes=plate_crop_bytes,
            error=f"OCR error: {error}",
        )

    if not ocr_result["success"]:
        return DetectionResult(
            success=False,
            raw_plate_text=ocr_result.get(
                "raw_text",
                "",
            ),
            confidence_score=round(
                detection_confidence,
                3,
            ),
            bounding_box=expanded_box,
            plate_crop_bytes=plate_crop_bytes,
            error=(
                "Plate detected, but no reliable valid "
                "Indian registration number was recognized."
            ),
        )

    final_plate = ocr_result["plate"]
    ocr_confidence = ocr_result["confidence"]
    variant_votes = ocr_result["votes"]

    vote_bonus = min(
        variant_votes / 4,
        1.0,
    )

    overall_confidence = (
        detection_confidence * 0.40
        + ocr_confidence * 0.45
        + vote_bonus * 0.15
    )

    overall_confidence = round(
        max(
            0.0,
            min(overall_confidence, 1.0),
        ),
        3,
    )

    (
        matched_vehicle,
        authorization_status,
    ) = match_vehicle_exact(
        final_plate
    )

    logger.info(
        (
            "ANPR result plate=%s "
            "detection_confidence=%.3f "
            "ocr_confidence=%.3f "
            "variant_votes=%s "
            "sharpness=%.2f"
        ),
        final_plate,
        detection_confidence,
        ocr_confidence,
        variant_votes,
        crop_sharpness,
    )

    return DetectionResult(
        success=True,
        raw_plate_text=ocr_result.get(
            "raw_text",
            "",
        ),
        cleaned_plate_text=final_plate,
        confidence_score=overall_confidence,
        bounding_box=expanded_box,
        matched_vehicle=build_vehicle_data(
            matched_vehicle
        ),
        authorization_status=authorization_status,
        plate_crop_bytes=plate_crop_bytes,
    )