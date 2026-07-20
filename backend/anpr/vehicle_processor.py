"""One-track ANPR processing, consensus, enrichment, and record saving."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
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
            return None

        best = candidates[0]
        bbox_list = clamp_bounding_box(best["bounding_box"], image)
        if bbox_list is None:
            return None
        x1, y1, x2, y2 = bbox_list
        plate_crop = image[y1:y2, x1:x2]
        if plate_crop.size == 0:
            return None

        raw_text, ocr_confidence = run_ocr(
            preprocess_plate_crop(plate_crop)
        )
        if not raw_text:
            return None

        validation = validate_and_normalize_plate(
            raw_text,
            max_corrections=1,
            allow_edge_noise=False,
        )
        if not validation.is_valid:
            return None

        yolo_confidence = float(best["confidence"])
        adjusted_ocr = max(
            0.0,
            float(ocr_confidence) - validation.corrections * 0.05,
        )
        overall = round((yolo_confidence + adjusted_ocr) / 2.0, 3)
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
        del winner_plate
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