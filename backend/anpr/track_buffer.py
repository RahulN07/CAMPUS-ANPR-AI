"""Bounded, quality-ranked frame candidates for tracked vehicles.

The camera/tracker thread calls ``observe`` for every tracked vehicle but no
OCR runs here.  Only the best few vehicle crops stay in memory.  When the
vehicle crosses the configured line, ``finalize`` creates exactly one task for
the later OCR worker pool.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from anpr.line_crossing import LineCrossingEvent
from anpr.vehicle_tracker import VehicleDetection


@dataclass(frozen=True, slots=True)
class TrackBufferConfig:
    candidates_per_track: int = 3
    max_active_tracks: int = 100
    max_idle_frames: int = 60
    duplicate_hold_frames: int = 150
    minimum_crop_width: int = 48
    minimum_crop_height: int = 32

    def __post_init__(self) -> None:
        if not 1 <= self.candidates_per_track <= 10:
            raise ValueError("candidates_per_track must be between 1 and 10")
        if not 1 <= self.max_active_tracks <= 1000:
            raise ValueError("max_active_tracks must be between 1 and 1000")
        if self.max_idle_frames < 1:
            raise ValueError("max_idle_frames must be at least 1")
        if self.duplicate_hold_frames < 1:
            raise ValueError("duplicate_hold_frames must be at least 1")
        if self.minimum_crop_width < 2:
            raise ValueError("minimum_crop_width must be at least 2")
        if self.minimum_crop_height < 2:
            raise ValueError("minimum_crop_height must be at least 2")


@dataclass(frozen=True, slots=True)
class VehicleFrameCandidate:
    track_id: int
    frame_index: int
    captured_at: float
    vehicle_type: str
    vehicle_confidence: float
    source_bbox: tuple[int, int, int, int]
    sharpness: float
    quality_score: float
    crop: np.ndarray = field(repr=False, compare=False)

    @property
    def width(self) -> int:
        return int(self.crop.shape[1])

    @property
    def height(self) -> int:
        return int(self.crop.shape[0])


@dataclass(frozen=True, slots=True)
class FinalizedVehicleTrack:
    track_id: int
    vehicle_type: str
    physical_direction: str
    crossing_frame_index: int
    created_at: float
    candidates: tuple[VehicleFrameCandidate, ...]

    @property
    def best_candidate(self) -> VehicleFrameCandidate:
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class TrackBufferStats:
    observations: int
    candidates_accepted: int
    crops_rejected: int
    tasks_finalized: int
    duplicates_ignored: int
    stale_tracks_removed: int
    capacity_evictions: int
    active_tracks: int
    retained_candidates: int


@dataclass(slots=True)
class _BufferedTrack:
    last_seen_frame: int
    candidates: list[VehicleFrameCandidate] = field(default_factory=list)
    vehicle_votes: dict[str, float] = field(default_factory=dict)


class TrackCandidateBuffer:
    """Thread-safe, bounded candidate storage keyed by ByteTrack Track ID."""

    def __init__(self, config: TrackBufferConfig | None = None) -> None:
        self.config = config or TrackBufferConfig()
        self._tracks: dict[int, _BufferedTrack] = {}
        self._finalized_at: dict[int, int] = {}
        self._lock = threading.RLock()
        self._observations = 0
        self._candidates_accepted = 0
        self._crops_rejected = 0
        self._tasks_finalized = 0
        self._duplicates_ignored = 0
        self._stale_tracks_removed = 0
        self._capacity_evictions = 0

    def observe(
        self,
        *,
        frame: np.ndarray,
        detection: VehicleDetection,
        frame_index: int,
        captured_at: float | None = None,
    ) -> bool:
        """Consider one vehicle crop and retain it only when useful.

        Returns ``True`` when the crop enters that track's retained top-N set.
        Untracked detections are deliberately ignored because they cannot be
        deduplicated or finalized safely.
        """

        self._validate_frame(frame)
        self._validate_frame_index(frame_index)

        with self._lock:
            self._observations += 1
            self._prune(frame_index)

            if detection.track_id is None:
                self._crops_rejected += 1
                return False

            track_id = int(detection.track_id)
            finalized_frame = self._finalized_at.get(track_id)
            if finalized_frame is not None:
                if frame_index <= (
                    finalized_frame + self.config.duplicate_hold_frames
                ):
                    self._duplicates_ignored += 1
                    return False
                del self._finalized_at[track_id]

            bbox = self._clamp_bbox(detection.bbox, frame.shape)
            if bbox is None:
                self._crops_rejected += 1
                return False

            x1, y1, x2, y2 = bbox
            width = x2 - x1
            height = y2 - y1
            if (
                width < self.config.minimum_crop_width
                or height < self.config.minimum_crop_height
            ):
                self._crops_rejected += 1
                return False

            state = self._tracks.get(track_id)
            if state is not None and frame_index < state.last_seen_frame:
                raise ValueError(
                    "frame_index cannot move backwards for an existing track"
                )

            crop = np.ascontiguousarray(frame[y1:y2, x1:x2]).copy()
            if crop.size == 0:
                self._crops_rejected += 1
                return False

            sharpness = self._sharpness(crop)
            confidence = min(1.0, max(0.0, float(detection.confidence)))
            quality_score = self._quality_score(
                sharpness=sharpness,
                area=width * height,
                confidence=confidence,
            )
            candidate = VehicleFrameCandidate(
                track_id=track_id,
                frame_index=frame_index,
                captured_at=(
                    float(captured_at)
                    if captured_at is not None
                    else time.monotonic()
                ),
                vehicle_type=str(detection.vehicle_type),
                vehicle_confidence=confidence,
                source_bbox=bbox,
                sharpness=sharpness,
                quality_score=quality_score,
                crop=crop,
            )

            if state is None:
                self._ensure_capacity()
                state = _BufferedTrack(last_seen_frame=frame_index)
                self._tracks[track_id] = state

            state.last_seen_frame = frame_index
            state.vehicle_votes[candidate.vehicle_type] = (
                state.vehicle_votes.get(candidate.vehicle_type, 0.0)
                + confidence
            )

            retained = self._retain_candidate(state, candidate)
            if retained:
                self._candidates_accepted += 1
            else:
                self._crops_rejected += 1
            return retained

    def finalize(
        self,
        crossing: LineCrossingEvent,
    ) -> FinalizedVehicleTrack | None:
        """Remove a track buffer and create its one immutable OCR task."""

        with self._lock:
            existing_finalized = self._finalized_at.get(crossing.track_id)
            if existing_finalized is not None and crossing.frame_index <= (
                existing_finalized + self.config.duplicate_hold_frames
            ):
                self._duplicates_ignored += 1
                return None

            state = self._tracks.pop(crossing.track_id, None)
            if state is None or not state.candidates:
                return None

            candidates = tuple(
                sorted(
                    state.candidates,
                    key=self._candidate_rank,
                    reverse=True,
                )
            )
            vehicle_type = max(
                state.vehicle_votes.items(),
                key=lambda item: (item[1], item[0]),
            )[0]
            self._finalized_at[crossing.track_id] = crossing.frame_index
            self._tasks_finalized += 1
            return FinalizedVehicleTrack(
                track_id=crossing.track_id,
                vehicle_type=vehicle_type,
                physical_direction=crossing.physical_direction,
                crossing_frame_index=crossing.frame_index,
                created_at=time.monotonic(),
                candidates=candidates,
            )

    def remove_track(self, track_id: int) -> bool:
        with self._lock:
            return self._tracks.pop(int(track_id), None) is not None

    def reset(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._finalized_at.clear()
            self._observations = 0
            self._candidates_accepted = 0
            self._crops_rejected = 0
            self._tasks_finalized = 0
            self._duplicates_ignored = 0
            self._stale_tracks_removed = 0
            self._capacity_evictions = 0

    def stats(self) -> TrackBufferStats:
        with self._lock:
            return TrackBufferStats(
                observations=self._observations,
                candidates_accepted=self._candidates_accepted,
                crops_rejected=self._crops_rejected,
                tasks_finalized=self._tasks_finalized,
                duplicates_ignored=self._duplicates_ignored,
                stale_tracks_removed=self._stale_tracks_removed,
                capacity_evictions=self._capacity_evictions,
                active_tracks=len(self._tracks),
                retained_candidates=sum(
                    len(state.candidates) for state in self._tracks.values()
                ),
            )

    def _retain_candidate(
        self,
        state: _BufferedTrack,
        candidate: VehicleFrameCandidate,
    ) -> bool:
        """Retain two recent views and quality-ranked historical fallbacks.

        OCR consensus needs more than one plate-readable crop near the line.
        Ranking only by whole-vehicle sharpness can otherwise fill the buffer
        with small early views. The two newest observations are protected;
        remaining capacity is filled by the strongest older candidates.
        Memory remains strictly bounded by ``candidates_per_track``.
        """

        limit = self.config.candidates_per_track
        pool = [*state.candidates, candidate]

        recent_limit = min(2, limit)
        selected = sorted(
            pool,
            key=lambda item: (
                item.frame_index,
                *self._candidate_rank(item),
            ),
            reverse=True,
        )[:recent_limit]

        selected_ids = {id(item) for item in selected}
        historical = sorted(
            (
                item
                for item in pool
                if id(item) not in selected_ids
            ),
            key=self._candidate_rank,
            reverse=True,
        )
        for item in historical:
            if len(selected) >= limit:
                break
            selected.append(item)

        selected.sort(key=self._candidate_rank, reverse=True)
        state.candidates = selected
        return any(item is candidate for item in selected)

    @staticmethod
    def _candidate_rank(candidate: VehicleFrameCandidate) -> tuple[float, int]:
        return candidate.quality_score, candidate.frame_index

    @staticmethod
    def _quality_score(
        *,
        sharpness: float,
        area: int,
        confidence: float,
    ) -> float:
        # Logarithms prevent a very large but blurry crop from overwhelming a
        # smaller sharp crop.  Sharpness remains the dominant quality signal.
        sharpness_component = math.log1p(max(0.0, sharpness))
        area_component = math.log1p(max(1, area))
        return confidence * (
            0.70 * sharpness_component + 0.30 * area_component
        )

    @staticmethod
    def _sharpness(crop: np.ndarray) -> float:
        if crop.ndim == 2:
            gray = crop
        elif crop.ndim == 3 and crop.shape[2] == 4:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY)
        elif crop.ndim == 3 and crop.shape[2] == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            return 0.0

        value = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return value if math.isfinite(value) else 0.0

    def _ensure_capacity(self) -> None:
        if len(self._tracks) < self.config.max_active_tracks:
            return

        oldest_track_id = min(
            self._tracks,
            key=lambda track_id: self._tracks[track_id].last_seen_frame,
        )
        del self._tracks[oldest_track_id]
        self._capacity_evictions += 1

    def _prune(self, frame_index: int) -> None:
        cutoff = frame_index - self.config.max_idle_frames
        stale_ids = [
            track_id
            for track_id, state in self._tracks.items()
            if state.last_seen_frame < cutoff
        ]
        for track_id in stale_ids:
            del self._tracks[track_id]
        self._stale_tracks_removed += len(stale_ids)

        expired_finalized_ids = [
            track_id
            for track_id, finalized_frame in self._finalized_at.items()
            if frame_index > (
                finalized_frame + self.config.duplicate_hold_frames
            )
        ]
        for track_id in expired_finalized_ids:
            del self._finalized_at[track_id]

    @staticmethod
    def _clamp_bbox(
        bbox: tuple[int, int, int, int],
        frame_shape: tuple[int, ...],
    ) -> tuple[int, int, int, int] | None:
        if len(bbox) != 4:
            return None
        frame_height, frame_width = int(frame_shape[0]), int(frame_shape[1])
        x1 = max(0, min(frame_width, int(bbox[0])))
        y1 = max(0, min(frame_height, int(bbox[1])))
        x2 = max(0, min(frame_width, int(bbox[2])))
        y2 = max(0, min(frame_height, int(bbox[3])))
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    @staticmethod
    def _validate_frame(frame: np.ndarray) -> None:
        if not isinstance(frame, np.ndarray):
            raise TypeError("frame must be a NumPy array")
        if frame.ndim not in (2, 3) or frame.size == 0:
            raise ValueError("frame must be a non-empty image")
        if frame.shape[0] < 2 or frame.shape[1] < 2:
            raise ValueError("frame dimensions are too small")

    @staticmethod
    def _validate_frame_index(frame_index: int) -> None:
        if not isinstance(frame_index, int) or isinstance(frame_index, bool):
            raise TypeError("frame_index must be an integer")
        if frame_index < 0:
            raise ValueError("frame_index cannot be negative")