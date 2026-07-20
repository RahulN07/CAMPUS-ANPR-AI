"""One-track ANPR processing, consensus, enrichment, and record saving."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable

import cv2
import numpy as np
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from anpr.detector import (
    clamp_bounding_box,
    detect_plate_bboxes,
    preprocess_plate_crop,
    run_conflict_ocr,
    run_ocr,
)
from anpr.plate_validation import validate_and_normalize_plate
from anpr.track_buffer import FinalizedVehicleTrack, VehicleFrameCandidate
from anpr.vehicle_attributes import (
    detect_vehicle_color,
    predict_vehicle_make_model,
)
from anpr.vehicle_cache import (
    CachedVehicle,
    VehicleCache,
    VehicleCacheError,
    VehicleLookupResult,
    get_vehicle_cache,
)
from records.models import EntryExitRecord


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VehicleProcessorConfig:
    gate_id: int
    direction: str
    recorded_by_id: int | None
    detection_source: str = EntryExitRecord.DetectionSource.CCTV
    plate_confidence: float = 0.40
    required_unknown_votes: int = 2
    maximum_candidates: int = 3
    registered_fast_path_confidence: float = 0.55
    single_unknown_confidence: float = 0.95
    duplicate_seconds: float = 5.0
    evaluate_all_unknown_candidates: bool = False
    conflict_ocr_min_confidence: float = 0.85
    conflict_ocr_override_min_confidence: float = 0.90
    conflict_track_votes: int = 2

    def __post_init__(self) -> None:
        if self.gate_id < 1:
            raise ValueError("gate_id must be a positive integer")
        if self.recorded_by_id is not None and self.recorded_by_id < 1:
            raise ValueError("recorded_by_id must be positive when provided")
        if self.direction not in EntryExitRecord.Direction.values:
            raise ValueError("direction must be ENTRY or EXIT")
        if self.detection_source not in EntryExitRecord.DetectionSource.values:
            raise ValueError("invalid detection_source")
        for name, value in (
            ("plate_confidence", self.plate_confidence),
            (
                "registered_fast_path_confidence",
                self.registered_fast_path_confidence,
            ),
            ("single_unknown_confidence", self.single_unknown_confidence),
            (
                "conflict_ocr_min_confidence",
                self.conflict_ocr_min_confidence,
            ),
            (
                "conflict_ocr_override_min_confidence",
                self.conflict_ocr_override_min_confidence,
            ),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 1 <= self.required_unknown_votes <= 10:
            raise ValueError("required_unknown_votes must be between 1 and 10")
        if not 1 <= self.maximum_candidates <= 10:
            raise ValueError("maximum_candidates must be between 1 and 10")
        if self.required_unknown_votes > self.maximum_candidates:
            raise ValueError(
                "required_unknown_votes cannot exceed maximum_candidates"
            )
        if not 1 <= self.conflict_track_votes <= self.maximum_candidates:
            raise ValueError(
                "conflict_track_votes must be between 1 and "
                "maximum_candidates"
            )
        if self.duplicate_seconds <= 0:
            raise ValueError("duplicate_seconds must be greater than 0")


@dataclass(frozen=True, slots=True)
class PlateObservation:
    plate_text: str
    raw_text: str
    confidence: float
    plate_yolo_confidence: float
    ocr_confidence: float
    corrections: int
    bounding_box: tuple[int, int, int, int]
    plate_image_bytes: bytes | None
    candidate: VehicleFrameCandidate


@dataclass(frozen=True, slots=True)
class VehicleRecordPayload:
    track_id: int
    plate_text: str
    confidence: float
    authorization_status: str
    was_authorized: bool
    cached_vehicle: CachedVehicle | None
    detected_vehicle_type: str
    vehicle_type_confidence: float
    vehicle_color: str
    vehicle_color_confidence: float
    detected_vehicle_company: str
    detected_vehicle_model: str
    vehicle_make_model_confidence: float
    notes: str
    captured_image_bytes: bytes | None
    plate_image_bytes: bytes | None


@dataclass(frozen=True, slots=True)
class VehicleProcessingResult:
    track_id: int
    saved: bool
    reason: str
    plate_text: str = ""
    authorization_status: str = "UNKNOWN"
    authorized: bool = False
    record_id: int | None = None
    confidence: float = 0.0
    votes: int = 0
    candidates_attempted: int = 0
    processing_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class PlateReservation:
    accepted: bool
    key: tuple[int, str, str]
    token: str
    existing_record_id: int | None = None


@dataclass(frozen=True, slots=True)
class _GuardEntry:
    created_at: float
    record_id: int | None
    token: str


PlateRecognizer = Callable[[VehicleFrameCandidate], PlateObservation | None]
ColorDetector = Callable[[np.ndarray], tuple[str, float]]
MakeModelDetector = Callable[[np.ndarray], dict]
RecordSaver = Callable[[VehicleRecordPayload], int]
ConflictRecognizer = Callable[[np.ndarray], tuple[str, float]]


def _is_single_edit_apart(first: str, second: str) -> bool:
    """Return whether two non-identical plate strings differ by one edit."""

    if first == second or abs(len(first) - len(second)) > 1:
        return False
    if len(first) == len(second):
        return sum(
            left != right
            for left, right in zip(first, second)
        ) == 1

    shorter, longer = (
        (first, second)
        if len(first) < len(second)
        else (second, first)
    )
    return any(
        longer[:index] + longer[index + 1 :] == shorter
        for index in range(len(longer))
    )


class RecentPlateGuard:
    """Atomic in-process duplicate reservation for parallel workers."""

    def __init__(self, cooldown_seconds: float = 5.0) -> None:
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be greater than 0")
        self.cooldown_seconds = float(cooldown_seconds)
        self.reservation_timeout_seconds = max(
            300.0,
            self.cooldown_seconds * 10.0,
        )
        self._entries: dict[tuple[int, str, str], _GuardEntry] = {}
        self._lock = threading.Lock()

    def reserve(
        self,
        *,
        gate_id: int,
        direction: str,
        plate_text: str,
    ) -> PlateReservation:
        key = (int(gate_id), str(direction), str(plate_text))
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            existing = self._entries.get(key)
            if existing is not None:
                return PlateReservation(
                    accepted=False,
                    key=key,
                    token="",
                    existing_record_id=existing.record_id,
                )
            token = uuid.uuid4().hex
            self._entries[key] = _GuardEntry(
                created_at=now,
                record_id=None,
                token=token,
            )
            return PlateReservation(
                accepted=True,
                key=key,
                token=token,
            )

    def commit(
        self,
        *,
        reservation: PlateReservation,
        record_id: int,
    ) -> bool:
        with self._lock:
            existing = self._entries.get(reservation.key)
            if existing is None or existing.token != reservation.token:
                return False
            self._entries[reservation.key] = _GuardEntry(
                created_at=time.monotonic(),
                record_id=int(record_id),
                token=reservation.token,
            )
            return True

    def release(
        self,
        *,
        reservation: PlateReservation,
    ) -> bool:
        with self._lock:
            existing = self._entries.get(reservation.key)
            if (
                existing is not None
                and existing.record_id is None
                and existing.token == reservation.token
            ):
                del self._entries[reservation.key]
                return True
            return False

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.created_at
            >= (
                self.reservation_timeout_seconds
                if entry.record_id is None
                else self.cooldown_seconds
            )
        ]
        for key in expired:
            del self._entries[key]


class VehicleProcessor:
    """Process one finalized Track ID outside the camera thread."""

    def __init__(
        self,
        *,
        config: VehicleProcessorConfig,
        cache: VehicleCache | None = None,
        duplicate_guard: RecentPlateGuard | None = None,
        plate_recognizer: PlateRecognizer | None = None,
        color_detector: ColorDetector | None = None,
        make_model_detector: MakeModelDetector | None = None,
        record_saver: RecordSaver | None = None,
        conflict_recognizer: ConflictRecognizer | None = None,
    ) -> None:
        self.config = config
        self.cache = cache or get_vehicle_cache()
        self.duplicate_guard = duplicate_guard or RecentPlateGuard(
            config.duplicate_seconds
        )
        self._plate_recognizer = plate_recognizer or self._recognize_plate
        self._color_detector = color_detector or detect_vehicle_color
        self._make_model_detector = (
            make_model_detector or predict_vehicle_make_model
        )
        self._record_saver = record_saver or self._save_record
        self._conflict_recognizer = (
            conflict_recognizer or run_conflict_ocr
        )

    def process(self, track: FinalizedVehicleTrack) -> VehicleProcessingResult:
        started = time.perf_counter()
        if not self.cache.stats().loaded:
            raise VehicleCacheError(
                "Vehicle cache is not loaded. Warm it before starting cameras."
            )

        observations: list[PlateObservation] = []
        chosen: PlateObservation | None = None
        chosen_lookup: VehicleLookupResult | None = None
        attempted = 0

        for candidate in track.candidates[: self.config.maximum_candidates]:
            attempted += 1
            try:
                observation = self._plate_recognizer(candidate)
            except Exception:
                logger.exception(
                    "Plate recognition failed for track %s frame %s",
                    track.track_id,
                    candidate.frame_index,
                )
                continue

            if observation is None:
                continue
            observations.append(observation)
            lookup = self.cache.lookup_result(observation.plate_text)

            # Any registered plate (authorized, pending, expired, or denied)
            # already has trusted attributes.  A confident exact cache hit can
            # finish immediately and skip both remaining OCR and enrichment.
            if (
                lookup.found
                and observation.confidence
                >= self.config.registered_fast_path_confidence
            ):
                chosen = observation
                chosen_lookup = lookup
                break

            # Unknown vehicles require repeated agreement, but there is no
            # benefit in running OCR on another crop after the configured
            # number of exact votes has already been collected. Select the
            # strongest observation from the agreeing group and finish this
            # track immediately. This keeps the same consensus rule while
            # removing one expensive PaddleOCR call in the normal 2-of-3
            # configuration.
            agreeing = [
                item
                for item in observations
                if item.plate_text == observation.plate_text
            ]
            if (
                not self.config.evaluate_all_unknown_candidates
                and len(agreeing) >= self.config.required_unknown_votes
            ):
                chosen = max(
                    agreeing,
                    key=lambda item: (
                        item.confidence,
                        item.candidate.quality_score,
                        item.candidate.frame_index,
                    ),
                )
                chosen_lookup = lookup
                break

        if chosen is None:
            chosen, votes = self._choose_unknown_consensus(observations)
            if chosen is None:
                reason = (
                    "NO_VALID_PLATE"
                    if not observations
                    else "CONSENSUS_NOT_REACHED"
                )
                return self._result(
                    track=track,
                    started=started,
                    saved=False,
                    reason=reason,
                    candidates_attempted=attempted,
                    votes=votes,
                )
            chosen_lookup = self.cache.lookup_result(chosen.plate_text)
        else:
            votes = sum(
                item.plate_text == chosen.plate_text for item in observations
            )

        assert chosen_lookup is not None
        reservation = self.duplicate_guard.reserve(
            gate_id=self.config.gate_id,
            direction=self.config.direction,
            plate_text=chosen.plate_text,
        )
        if not reservation.accepted:
            return self._result(
                track=track,
                started=started,
                saved=False,
                reason="DUPLICATE_IGNORED",
                plate_text=chosen.plate_text,
                authorization_status=chosen_lookup.authorization_status,
                authorized=chosen_lookup.authorized,
                record_id=reservation.existing_record_id,
                confidence=chosen.confidence,
                votes=votes,
                candidates_attempted=attempted,
            )

        try:
            payload = self._build_payload(
                track=track,
                observation=chosen,
                lookup=chosen_lookup,
                votes=votes,
            )
            record_id = int(self._record_saver(payload))
        except Exception:
            self.duplicate_guard.release(
                reservation=reservation,
            )
            raise

        committed = self.duplicate_guard.commit(
            reservation=reservation,
            record_id=record_id,
        )
        if not committed:
            logger.error(
                "Duplicate reservation expired before record %s committed",
                record_id,
            )
        return self._result(
            track=track,
            started=started,
            saved=True,
            reason="SAVED",
            plate_text=chosen.plate_text,
            authorization_status=chosen_lookup.authorization_status,
            authorized=chosen_lookup.authorized,
            record_id=record_id,
            confidence=chosen.confidence,
            votes=votes,
            candidates_attempted=attempted,
        )

    def _build_payload(
        self,
        *,
        track: FinalizedVehicleTrack,
        observation: PlateObservation,
        lookup: VehicleLookupResult,
        votes: int,
    ) -> VehicleRecordPayload:
        cached = lookup.vehicle
        best_candidate = observation.candidate

        if cached is not None:
            detected_type = cached.vehicle_type
            type_confidence = 0.0
            color = cached.color
            color_confidence = 0.0
            company = cached.vehicle_company
            model = cached.vehicle_model
            make_model_confidence = 0.0
            attribute_source = "registered vehicle cache"
        else:
            detected_type = self._display_vehicle_type(track.vehicle_type)
            type_confidence = best_candidate.vehicle_confidence
            color, color_confidence = self._color_detector(
                best_candidate.crop
            )
            make_model = self._make_model_detector(best_candidate.crop)
            company = str(make_model.get("company") or "Unknown")
            model = str(make_model.get("model") or "Unknown")
            make_model_confidence = float(
                make_model.get("confidence") or 0.0
            )
            attribute_source = "computer vision enrichment"

        notes = (
            f"Track ID: {track.track_id}; "
            f"Physical crossing: {track.physical_direction}; "
            f"OCR votes: {votes}; "
            f"Attributes: {attribute_source}."
        )
        return VehicleRecordPayload(
            track_id=track.track_id,
            plate_text=observation.plate_text,
            confidence=observation.confidence,
            authorization_status=lookup.authorization_status,
            was_authorized=lookup.authorized,
            cached_vehicle=cached,
            detected_vehicle_type=detected_type,
            vehicle_type_confidence=type_confidence,
            vehicle_color=color,
            vehicle_color_confidence=float(color_confidence),
            detected_vehicle_company=company,
            detected_vehicle_model=model,
            vehicle_make_model_confidence=make_model_confidence,
            notes=notes,
            captured_image_bytes=self._encode_jpeg(best_candidate.crop),
            plate_image_bytes=observation.plate_image_bytes,
        )

    def _recognize_plate(
        self,
        candidate: VehicleFrameCandidate,
    ) -> PlateObservation | None:
        image = candidate.crop
        candidates = detect_plate_bboxes(
            image,
            confidence_threshold=self.config.plate_confidence,
        )
        if not candidates:
            logger.warning(
                "Track %s frame %s: PLATE_NOT_DETECTED "
                "(vehicle crop %sx%s, threshold %.2f)",
                candidate.track_id,
                candidate.frame_index,
                image.shape[1],
                image.shape[0],
                self.config.plate_confidence,
            )
            return None

        best = candidates[0]
        bbox_list = clamp_bounding_box(best["bounding_box"], image)
        if bbox_list is None:
            logger.warning(
                "Track %s frame %s: INVALID_PLATE_BOX "
                "(raw box %r, vehicle crop %sx%s)",
                candidate.track_id,
                candidate.frame_index,
                best.get("bounding_box"),
                image.shape[1],
                image.shape[0],
            )
            return None

        # Plate detectors commonly fit the visible plate border very tightly.
        # At CCTV resolution that can place the first or last character on the
        # crop boundary, causing recognition to drop it. Add proportional,
        # bounded context before OCR while keeping the crop inside the vehicle.
        detected_x1, detected_y1, detected_x2, detected_y2 = bbox_list
        detected_width = detected_x2 - detected_x1
        detected_height = detected_y2 - detected_y1
        horizontal_padding = max(
            2,
            int(round(detected_width * 0.12)),
        )
        vertical_padding = max(
            2,
            int(round(detected_height * 0.18)),
        )
        padded_bbox = clamp_bounding_box(
            [
                detected_x1 - horizontal_padding,
                detected_y1 - vertical_padding,
                detected_x2 + horizontal_padding,
                detected_y2 + vertical_padding,
            ],
            image,
        )
        if padded_bbox is not None:
            bbox_list = padded_bbox

        x1, y1, x2, y2 = bbox_list
        plate_crop = image[y1:y2, x1:x2]
        if plate_crop.size == 0:
            logger.warning(
                "Track %s frame %s: EMPTY_PLATE_CROP "
                "(box %s)",
                candidate.track_id,
                candidate.frame_index,
                (x1, y1, x2, y2),
            )
            return None

        raw_text, ocr_confidence = run_ocr(
            preprocess_plate_crop(plate_crop)
        )
        if not raw_text:
            logger.warning(
                "Track %s frame %s: OCR_EMPTY "
                "(plate crop %sx%s, YOLO %.3f)",
                candidate.track_id,
                candidate.frame_index,
                plate_crop.shape[1],
                plate_crop.shape[0],
                float(best["confidence"]),
            )
            return None

        validation = validate_and_normalize_plate(
            raw_text,
            max_corrections=1,
            allow_edge_noise=False,
        )
        if not validation.is_valid:
            logger.warning(
                "Track %s frame %s: PLATE_FORMAT_REJECTED "
                "(raw=%r, OCR %.3f, YOLO %.3f, crop %sx%s)",
                candidate.track_id,
                candidate.frame_index,
                raw_text,
                float(ocr_confidence),
                float(best["confidence"]),
                plate_crop.shape[1],
                plate_crop.shape[0],
            )
            return None

        yolo_confidence = float(best["confidence"])
        adjusted_ocr = max(
            0.0,
            float(ocr_confidence) - validation.corrections * 0.05,
        )
        overall = round((yolo_confidence + adjusted_ocr) / 2.0, 3)
        logger.warning(
            "Track %s frame %s: PLATE_VALIDATED "
            "(plate=%s, raw=%r, overall %.3f, OCR %.3f, "
            "YOLO %.3f, corrections=%s, crop %sx%s)",
            candidate.track_id,
            candidate.frame_index,
            validation.normalized_text,
            raw_text,
            overall,
            float(ocr_confidence),
            yolo_confidence,
            validation.corrections,
            plate_crop.shape[1],
            plate_crop.shape[0],
        )
        return PlateObservation(
            plate_text=validation.normalized_text,
            raw_text=raw_text,
            confidence=overall,
            plate_yolo_confidence=yolo_confidence,
            ocr_confidence=float(ocr_confidence),
            corrections=validation.corrections,
            bounding_box=(x1, y1, x2, y2),
            plate_image_bytes=self._encode_jpeg(plate_crop),
            candidate=candidate,
        )

    def _choose_unknown_consensus(
        self,
        observations: list[PlateObservation],
    ) -> tuple[PlateObservation | None, int]:
        if not observations:
            return None, 0

        groups: dict[str, list[PlateObservation]] = {}
        for observation in observations:
            groups.setdefault(observation.plate_text, []).append(observation)

        winner_plate, winner_group = max(
            groups.items(),
            key=lambda item: (
                len(item[1]),
                sum(value.confidence for value in item[1]),
                max(value.confidence for value in item[1]),
                item[0],
            ),
        )
        logger.warning(
            "Unknown-plate consensus groups: %s; winner=%s",
            {
                plate: [
                    {
                        "frame": value.candidate.frame_index,
                        "confidence": value.confidence,
                        "ocr": round(value.ocr_confidence, 3),
                        "yolo": round(value.plate_yolo_confidence, 3),
                    }
                    for value in values
                ]
                for plate, values in sorted(groups.items())
            },
            winner_plate,
        )

        if (
            self.config.evaluate_all_unknown_candidates
            and groups
        ):
            resolved = self._resolve_unknown_identity_conflict(groups)
            if resolved is not None:
                return resolved, max(
                    1,
                    len(groups.get(resolved.plate_text, ())),
                )
            # Full-evaluation mode is fail closed. Mobile OCR must not save a
            # plate after the independent arbitration stage was inconclusive.
            return None, len(winner_group)

        votes = len(winner_group)
        best = max(
            winner_group,
            key=lambda item: (
                item.confidence,
                item.candidate.quality_score,
                item.candidate.frame_index,
            ),
        )
        if votes >= self.config.required_unknown_votes:
            return best, votes
        if best.confidence >= self.config.single_unknown_confidence:
            return best, votes
        return None, votes

    def _resolve_unknown_identity_conflict(
        self,
        groups: dict[str, list[PlateObservation]],
    ) -> PlateObservation | None:
        """Require stable V6 consensus across multiple retained frames."""

        observations = [
            observation
            for values in groups.values()
            for observation in values
            if observation.plate_image_bytes
        ]
        if not observations:
            return None

        def resolution_rank(
            observation: PlateObservation,
        ) -> tuple[int, int, float, float]:
            x1, y1, x2, y2 = observation.bounding_box
            width = max(0, x2 - x1)
            height = max(0, y2 - y1)
            return (
                height,
                width * height,
                observation.ocr_confidence,
                observation.confidence,
            )

        ranked_observations = sorted(
            observations,
            key=resolution_rank,
            reverse=True,
        )
        mobile_identities = set(groups)
        conflict_groups: dict[
            str,
            list[tuple[PlateObservation, str, float, object]],
        ] = {}

        for observation in ranked_observations:
            assert observation.plate_image_bytes is not None
            encoded = np.frombuffer(
                observation.plate_image_bytes,
                dtype=np.uint8,
            )
            plate_crop = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if plate_crop is None or plate_crop.size == 0:
                continue

            try:
                raw_text, confidence = self._conflict_recognizer(plate_crop)
            except Exception:
                logger.exception(
                    "Track %s frame %s: conflict OCR failed",
                    observation.candidate.track_id,
                    observation.candidate.frame_index,
                )
                continue

            validation = validate_and_normalize_plate(
                raw_text,
                max_corrections=1,
                allow_edge_noise=False,
            )
            normalized = validation.normalized_text
            if (
                not validation.is_valid
                or float(confidence)
                < self.config.conflict_ocr_min_confidence
            ):
                continue

            same_mobile_identity = normalized in mobile_identities
            one_edit_override = (
                any(
                    _is_single_edit_apart(normalized, mobile_identity)
                    for mobile_identity in mobile_identities
                )
                and float(confidence)
                >= self.config.conflict_ocr_override_min_confidence
            )
            if not (same_mobile_identity or one_edit_override):
                logger.warning(
                    "Track %s frame %s: conflict identity %s is not a "
                    "safe one-edit match for mobile identities %s",
                    observation.candidate.track_id,
                    observation.candidate.frame_index,
                    normalized,
                    sorted(mobile_identities),
                )
                continue

            identity_votes = conflict_groups.setdefault(normalized, [])
            identity_votes.append(
                (observation, raw_text, float(confidence), validation)
            )
            if len(identity_votes) >= self.config.conflict_track_votes:
                logger.warning(
                    "Track %s: V6 cross-frame requirement reached for %s "
                    "after frame %s",
                    observation.candidate.track_id,
                    normalized,
                    observation.candidate.frame_index,
                )
                break

        if not conflict_groups:
            logger.warning(
                "Track %s: no stable V6 frame observations",
                ranked_observations[0].candidate.track_id,
            )
            return None

        normalized, winning_group = max(
            conflict_groups.items(),
            key=lambda item: (
                len(item[1]),
                sum(value[2] for value in item[1]),
                max(value[2] for value in item[1]),
            ),
        )
        if len(winning_group) < self.config.conflict_track_votes:
            logger.warning(
                "Track %s: V6 cross-frame consensus not reached: %s",
                ranked_observations[0].candidate.track_id,
                {
                    plate: [
                        {
                            "frame": value[0].candidate.frame_index,
                            "confidence": round(value[2], 3),
                        }
                        for value in values
                    ]
                    for plate, values in sorted(conflict_groups.items())
                },
            )
            return None

        source_observation, raw_text, confidence, validation = max(
            winning_group,
            key=lambda value: (
                value[2],
                resolution_rank(value[0]),
            ),
        )
        if normalized in groups:
            chosen = max(
                groups[normalized],
                key=lambda observation: (
                    resolution_rank(observation),
                    observation.candidate.quality_score,
                    observation.candidate.frame_index,
                ),
            )
        else:
            corrected_confidence = round(
                (
                    source_observation.plate_yolo_confidence
                    + confidence
                )
                / 2.0,
                3,
            )
            chosen = replace(
                source_observation,
                plate_text=normalized,
                raw_text=raw_text,
                confidence=corrected_confidence,
                ocr_confidence=confidence,
                corrections=validation.corrections,
            )
            logger.warning(
                "Track %s: V6 cross-frame consensus overrode mobile "
                "identity to %s",
                source_observation.candidate.track_id,
                normalized,
            )

        logger.warning(
            "Track %s: V6 cross-frame consensus resolved %s "
            "with %s frame votes",
            source_observation.candidate.track_id,
            normalized,
            len(winning_group),
        )
        return chosen

    def _save_record(self, payload: VehicleRecordPayload) -> int:
        unique = (
            f"{timezone.now():%Y%m%d_%H%M%S_%f}_"
            f"t{payload.track_id}_{uuid.uuid4().hex[:8]}"
        )
        with transaction.atomic():
            record = EntryExitRecord(
                vehicle_id=(
                    payload.cached_vehicle.id
                    if payload.cached_vehicle is not None
                    else None
                ),
                detected_plate_text=payload.plate_text,
                direction=self.config.direction,
                gate_id=self.config.gate_id,
                was_authorized=payload.was_authorized,
                confidence_score=payload.confidence,
                detection_source=self.config.detection_source,
                detected_vehicle_type=payload.detected_vehicle_type,
                vehicle_type_confidence=payload.vehicle_type_confidence,
                vehicle_color=payload.vehicle_color,
                vehicle_color_confidence=payload.vehicle_color_confidence,
                detected_vehicle_company=payload.detected_vehicle_company,
                detected_vehicle_model=payload.detected_vehicle_model,
                vehicle_make_model_confidence=(
                    payload.vehicle_make_model_confidence
                ),
                recorded_by_id=self.config.recorded_by_id,
                notes=payload.notes,
            )
            if payload.captured_image_bytes:
                record.captured_image.save(
                    f"vehicle_{unique}.jpg",
                    ContentFile(payload.captured_image_bytes),
                    save=False,
                )
            if payload.plate_image_bytes:
                record.plate_image.save(
                    f"plate_{unique}.jpg",
                    ContentFile(payload.plate_image_bytes),
                    save=False,
                )
            record.save()
        return int(record.pk)

    @staticmethod
    def _encode_jpeg(image: np.ndarray) -> bytes | None:
        if image is None or image.size == 0:
            return None
        success, encoded = cv2.imencode(".jpg", image)
        return encoded.tobytes() if success else None

    @staticmethod
    def _display_vehicle_type(value: str) -> str:
        aliases = {
            "motorbike": "Motorcycle",
            "motorcycle": "Motorcycle",
            "scooter": "Motorcycle",
            "car": "Car",
            "bus": "Bus",
            "truck": "Truck",
            "bicycle": "Bicycle",
        }
        normalized = str(value).strip().lower()
        return aliases.get(normalized, str(value).strip().title() or "Unknown")

    @staticmethod
    def _result(
        *,
        track: FinalizedVehicleTrack,
        started: float,
        saved: bool,
        reason: str,
        plate_text: str = "",
        authorization_status: str = "UNKNOWN",
        authorized: bool = False,
        record_id: int | None = None,
        confidence: float = 0.0,
        votes: int = 0,
        candidates_attempted: int = 0,
    ) -> VehicleProcessingResult:
        return VehicleProcessingResult(
            track_id=track.track_id,
            saved=saved,
            reason=reason,
            plate_text=plate_text,
            authorization_status=authorization_status,
            authorized=authorized,
            record_id=record_id,
            confidence=confidence,
            votes=votes,
            candidates_attempted=candidates_attempted,
            processing_ms=(time.perf_counter() - started) * 1000.0,
        )