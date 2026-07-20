"""
Conservative Indian registration-plate validation.

OCR corrections are applied only where the registration grammar
requires a letter or digit. Fuzzy matching is never used for vehicle
authorization.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


PLATE_CLEAN_PATTERN = re.compile(r"[^A-Z0-9]")

STANDARD_PLATE_PATTERN = re.compile(
    r"^[A-Z]{2}[0-9]{2}[A-Z]{1,3}[0-9]{4}$"
)

BH_SERIES_PATTERN = re.compile(
    r"^[0-9]{2}BH[0-9]{4}[A-Z]{2}$"
)


# Includes current codes plus important legacy codes that may still
# appear on valid older vehicles.
VALID_STATE_CODES = frozenset(
    {
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
        "OR",
        "PB",
        "PY",
        "RJ",
        "SK",
        "TG",
        "TN",
        "TR",
        "TS",
        "UA",
        "UK",
        "UP",
        "WB",
    }
)


# Conservative OCR substitutions. Corrections are only used in a
# position whose required character type is already known.
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


class PlateFormat(str, Enum):
    STANDARD = "STANDARD"
    BH_SERIES = "BH_SERIES"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PlateValidationResult:
    raw_text: str
    cleaned_text: str
    normalized_text: str

    is_valid: bool
    plate_format: PlateFormat

    corrections: int = 0
    removed_edge_characters: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _Candidate:
    normalized_text: str
    plate_format: PlateFormat
    corrections: int
    removed_edge_characters: int


def clean_plate_text(raw_text: str) -> str:
    """
    Convert OCR output to uppercase alphanumeric characters.
    """

    if not raw_text:
        return ""

    return PLATE_CLEAN_PATTERN.sub(
        "",
        str(raw_text).upper().strip(),
    )


def is_valid_standard_plate(plate: str) -> bool:
    plate = clean_plate_text(plate)

    if not STANDARD_PLATE_PATTERN.fullmatch(plate):
        return False

    if plate[:2] not in VALID_STATE_CODES:
        return False

    district_code = plate[2:4]
    registration_number = plate[-4:]

    # These values are not issued as normal registration components.
    if district_code == "00":
        return False

    if registration_number == "0000":
        return False

    return True


def is_valid_bh_series_plate(plate: str) -> bool:
    plate = clean_plate_text(plate)

    if not BH_SERIES_PATTERN.fullmatch(plate):
        return False

    registration_year = int(plate[:2])
    registration_number = plate[4:8]

    current_year = (
        datetime.now(timezone.utc).year % 100
    )

    # BH registrations began in 2021. One future year is allowed for
    # deployments whose server clock and registration data cross a
    # calendar boundary.
    if not 21 <= registration_year <= current_year + 1:
        return False

    if registration_number == "0000":
        return False

    return True


def is_valid_indian_plate(plate: str) -> bool:
    cleaned_plate = clean_plate_text(plate)

    return (
        is_valid_standard_plate(cleaned_plate)
        or is_valid_bh_series_plate(cleaned_plate)
    )


def _convert_to_letters(
    value: str,
) -> Optional[tuple[str, int]]:
    converted = []
    correction_count = 0

    for character in value:
        if character.isalpha():
            converted.append(character)
            continue

        replacement = DIGIT_TO_LETTER.get(character)

        if replacement is None:
            return None

        converted.append(replacement)
        correction_count += 1

    return "".join(converted), correction_count


def _convert_to_digits(
    value: str,
) -> Optional[tuple[str, int]]:
    converted = []
    correction_count = 0

    for character in value:
        if character.isdigit():
            converted.append(character)
            continue

        replacement = LETTER_TO_DIGIT.get(character)

        if replacement is None:
            return None

        converted.append(replacement)
        correction_count += 1

    return "".join(converted), correction_count


def _convert_to_literal(
    value: str,
    expected: str,
) -> Optional[tuple[str, int]]:
    if len(value) != len(expected):
        return None

    correction_count = 0

    for actual, required in zip(value, expected):
        if actual == required:
            continue

        corrected_letter = DIGIT_TO_LETTER.get(actual)
        corrected_digit = LETTER_TO_DIGIT.get(actual)

        if (
            corrected_letter != required
            and corrected_digit != required
        ):
            return None

        correction_count += 1

    return expected, correction_count


def _build_standard_candidate(
    cleaned_text: str,
    removed_edge_characters: int,
) -> Optional[_Candidate]:
    if not 9 <= len(cleaned_text) <= 11:
        return None

    state_part = cleaned_text[:2]
    district_part = cleaned_text[2:4]
    series_part = cleaned_text[4:-4]
    number_part = cleaned_text[-4:]

    if not 1 <= len(series_part) <= 3:
        return None

    corrected_state = _convert_to_letters(state_part)
    corrected_district = _convert_to_digits(
        district_part
    )
    corrected_series = _convert_to_letters(
        series_part
    )
    corrected_number = _convert_to_digits(
        number_part
    )

    converted_parts = (
        corrected_state,
        corrected_district,
        corrected_series,
        corrected_number,
    )

    if any(part is None for part in converted_parts):
        return None

    normalized_text = "".join(
        part[0]
        for part in converted_parts
        if part is not None
    )

    correction_count = sum(
        part[1]
        for part in converted_parts
        if part is not None
    )

    if not is_valid_standard_plate(normalized_text):
        return None

    return _Candidate(
        normalized_text=normalized_text,
        plate_format=PlateFormat.STANDARD,
        corrections=correction_count,
        removed_edge_characters=(
            removed_edge_characters
        ),
    )


def _build_bh_candidate(
    cleaned_text: str,
    removed_edge_characters: int,
) -> Optional[_Candidate]:
    if len(cleaned_text) != 10:
        return None

    year_part = cleaned_text[:2]
    bh_part = cleaned_text[2:4]
    number_part = cleaned_text[4:8]
    suffix_part = cleaned_text[8:10]

    corrected_year = _convert_to_digits(year_part)
    corrected_bh = _convert_to_literal(
        bh_part,
        "BH",
    )
    corrected_number = _convert_to_digits(
        number_part
    )
    corrected_suffix = _convert_to_letters(
        suffix_part
    )

    converted_parts = (
        corrected_year,
        corrected_bh,
        corrected_number,
        corrected_suffix,
    )

    if any(part is None for part in converted_parts):
        return None

    normalized_text = "".join(
        part[0]
        for part in converted_parts
        if part is not None
    )

    correction_count = sum(
        part[1]
        for part in converted_parts
        if part is not None
    )

    if not is_valid_bh_series_plate(normalized_text):
        return None

    return _Candidate(
        normalized_text=normalized_text,
        plate_format=PlateFormat.BH_SERIES,
        corrections=correction_count,
        removed_edge_characters=(
            removed_edge_characters
        ),
    )


def validate_and_normalize_plate(
    raw_text: str,
    *,
    max_corrections: int = 1,
    allow_edge_noise: bool = False,
) -> PlateValidationResult:
    """
    Validate and conservatively normalize PaddleOCR output.

    At most max_corrections character substitutions are accepted.
    Edge-character removal is disabled by default because security
    systems should prefer rejecting uncertain text over inventing a
    plausible registration.
    """

    if max_corrections < 0:
        raise ValueError(
            "max_corrections cannot be negative."
        )

    cleaned_text = clean_plate_text(raw_text)

    if not cleaned_text:
        return PlateValidationResult(
            raw_text=str(raw_text or ""),
            cleaned_text="",
            normalized_text="",
            is_valid=False,
            plate_format=PlateFormat.UNKNOWN,
            reason="OCR returned no alphanumeric text.",
        )

    attempts = [(cleaned_text, 0)]

    if allow_edge_noise and len(cleaned_text) > 1:
        attempts.extend(
            [
                (cleaned_text[1:], 1),
                (cleaned_text[:-1], 1),
            ]
        )

    candidates = []

    for attempt_text, removed_count in attempts:
        standard_candidate = _build_standard_candidate(
            attempt_text,
            removed_count,
        )

        bh_candidate = _build_bh_candidate(
            attempt_text,
            removed_count,
        )

        for candidate in (
            standard_candidate,
            bh_candidate,
        ):
            if candidate is None:
                continue

            if candidate.corrections > max_corrections:
                continue

            candidates.append(candidate)

    if not candidates:
        return PlateValidationResult(
            raw_text=str(raw_text),
            cleaned_text=cleaned_text,
            normalized_text="",
            is_valid=False,
            plate_format=PlateFormat.UNKNOWN,
            reason=(
                "Text does not match a supported Indian "
                "registration format."
            ),
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.removed_edge_characters,
            candidate.corrections,
            candidate.normalized_text,
        )
    )

    best_candidate = candidates[0]

    best_rank = (
        best_candidate.removed_edge_characters,
        best_candidate.corrections,
    )

    equally_ranked = {
        candidate.normalized_text
        for candidate in candidates
        if (
            candidate.removed_edge_characters,
            candidate.corrections,
        )
        == best_rank
    }

    if len(equally_ranked) > 1:
        return PlateValidationResult(
            raw_text=str(raw_text),
            cleaned_text=cleaned_text,
            normalized_text="",
            is_valid=False,
            plate_format=PlateFormat.UNKNOWN,
            reason=(
                "OCR text has multiple equally likely "
                "registration interpretations."
            ),
        )

    return PlateValidationResult(
        raw_text=str(raw_text),
        cleaned_text=cleaned_text,
        normalized_text=(
            best_candidate.normalized_text
        ),
        is_valid=True,
        plate_format=best_candidate.plate_format,
        corrections=best_candidate.corrections,
        removed_edge_characters=(
            best_candidate.removed_edge_characters
        ),
    )