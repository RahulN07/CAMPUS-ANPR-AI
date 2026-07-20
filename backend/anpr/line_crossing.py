"""Track-aware finite-line crossing detection for campus gates.

Coordinates stored on ``Gate`` are normalised from 0.0 to 1.0.  This keeps a
configured line valid when cameras provide different frame resolutions.
Physical line direction is independent of gate type: the gate model decides
whether a saved record is ENTRY or EXIT.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any


DIRECTION_ANY = "ANY"
DIRECTION_A_TO_B = "A_TO_B"
DIRECTION_B_TO_A = "B_TO_A"
VALID_DIRECTIONS = frozenset(
    {
        DIRECTION_ANY,
        DIRECTION_A_TO_B,
        DIRECTION_B_TO_A,
    }
)

SIDE_A = 1
SIDE_B = -1


@dataclass(frozen=True, slots=True)
class NormalizedPoint:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("line coordinates must be finite")
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise ValueError("line coordinates must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class LineCrossingConfig:
    start: NormalizedPoint
    end: NormalizedPoint
    allowed_direction: str = DIRECTION_ANY
    enabled: bool = True
    dead_zone: float = 0.008
    minimum_movement: float = 0.003
    segment_margin: float = 0.02
    max_idle_frames: int = 60

    def __post_init__(self) -> None:
        direction = str(self.allowed_direction).upper().strip()
        object.__setattr__(self, "allowed_direction", direction)

        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                "allowed_direction must be ANY, A_TO_B, or B_TO_A"
            )
        if self.start == self.end:
            raise ValueError("line start and end points must be different")
        if not 0.0 <= self.dead_zone <= 0.1:
            raise ValueError("dead_zone must be between 0.0 and 0.1")
        if not 0.0 <= self.minimum_movement <= 0.25:
            raise ValueError(
                "minimum_movement must be between 0.0 and 0.25"
            )
        if not 0.0 <= self.segment_margin <= 0.25:
            raise ValueError("segment_margin must be between 0.0 and 0.25")
        if self.max_idle_frames < 1:
            raise ValueError("max_idle_frames must be at least 1")

    @classmethod
    def from_gate(cls, gate: Any) -> "LineCrossingConfig":
        """Build validated crossing configuration from an existing Gate."""

        return cls(
            start=NormalizedPoint(
                float(gate.line_start_x),
                float(gate.line_start_y),
            ),
            end=NormalizedPoint(
                float(gate.line_end_x),
                float(gate.line_end_y),
            ),
            allowed_direction=str(gate.crossing_direction),
            enabled=bool(gate.line_crossing_enabled),
        )


@dataclass(frozen=True, slots=True)
class LineCrossingEvent:
    track_id: int
    physical_direction: str
    previous_point: NormalizedPoint
    current_point: NormalizedPoint
    intersection_point: NormalizedPoint
    frame_index: int


@dataclass(frozen=True, slots=True)
class LineCrossingStats:
    updates: int
    crossings: int
    rejected_direction: int
    stale_tracks_removed: int
    active_tracks: int


@dataclass(slots=True)
class _TrackState:
    last_point: NormalizedPoint
    stable_point: NormalizedPoint | None
    stable_side: int | None
    last_seen_frame: int
    crossed: bool = False


class LineCrossingDetector:
    """Detect one valid finite-line crossing per vehicle Track ID."""

    def __init__(self, config: LineCrossingConfig) -> None:
        self.config = config
        self._tracks: dict[int, _TrackState] = {}
        self._lock = threading.RLock()
        self._updates = 0
        self._crossings = 0
        self._rejected_direction = 0
        self._stale_tracks_removed = 0

        dx = config.end.x - config.start.x
        dy = config.end.y - config.start.y
        self._line_length = math.hypot(dx, dy)

    @classmethod
    def from_gate(cls, gate: Any) -> "LineCrossingDetector":
        return cls(LineCrossingConfig.from_gate(gate))

    def update(
        self,
        *,
        track_id: int,
        center: tuple[int | float, int | float],
        frame_width: int,
        frame_height: int,
        frame_index: int,
    ) -> LineCrossingEvent | None:
        """Update a track and return an event only on an accepted crossing."""

        self._validate_update(
            track_id=track_id,
            center=center,
            frame_width=frame_width,
            frame_height=frame_height,
            frame_index=frame_index,
        )

        point = self._normalise_center(
            center,
            frame_width,
            frame_height,
        )

        with self._lock:
            self._updates += 1
            self._prune_stale(frame_index)

            state = self._tracks.get(track_id)
            current_side = self._stable_side(point)

            if state is None:
                self._tracks[track_id] = _TrackState(
                    last_point=point,
                    stable_point=(point if current_side is not None else None),
                    stable_side=current_side,
                    last_seen_frame=frame_index,
                )
                return None

            if frame_index < state.last_seen_frame:
                raise ValueError(
                    "frame_index cannot move backwards for an existing track"
                )

            state.last_point = point
            state.last_seen_frame = frame_index

            if state.crossed or not self.config.enabled:
                return None
            if current_side is None:
                return None
            if state.stable_side is None or state.stable_point is None:
                state.stable_side = current_side
                state.stable_point = point
                return None
            if current_side == state.stable_side:
                state.stable_point = point
                return None
            if (
                self._distance(state.stable_point, point)
                < self.config.minimum_movement
            ):
                return None

            previous_point = state.stable_point
            previous_side = state.stable_side

            # Move the stable state even when a crossing is outside the finite
            # segment or travels in the rejected direction.  This prevents the
            # same transition from being reported repeatedly on later frames.
            state.stable_side = current_side
            state.stable_point = point

            intersection = self._segment_intersection(
                previous_point,
                point,
            )
            if intersection is None:
                return None

            physical_direction = (
                DIRECTION_A_TO_B
                if previous_side == SIDE_A and current_side == SIDE_B
                else DIRECTION_B_TO_A
            )

            if self.config.allowed_direction not in (
                DIRECTION_ANY,
                physical_direction,
            ):
                self._rejected_direction += 1
                return None

            state.crossed = True
            self._crossings += 1
            return LineCrossingEvent(
                track_id=track_id,
                physical_direction=physical_direction,
                previous_point=previous_point,
                current_point=point,
                intersection_point=intersection,
                frame_index=frame_index,
            )

    def remove_track(self, track_id: int) -> bool:
        with self._lock:
            return self._tracks.pop(int(track_id), None) is not None

    def reset(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._updates = 0
            self._crossings = 0
            self._rejected_direction = 0
            self._stale_tracks_removed = 0

    def stats(self) -> LineCrossingStats:
        with self._lock:
            return LineCrossingStats(
                updates=self._updates,
                crossings=self._crossings,
                rejected_direction=self._rejected_direction,
                stale_tracks_removed=self._stale_tracks_removed,
                active_tracks=len(self._tracks),
            )

    def line_pixels(
        self,
        frame_width: int,
        frame_height: int,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        """Return pixel endpoints for drawing the configured preview line."""

        if frame_width < 2 or frame_height < 2:
            raise ValueError("frame dimensions must be at least 2 by 2")

        def to_pixel(point: NormalizedPoint) -> tuple[int, int]:
            return (
                int(round(point.x * (frame_width - 1))),
                int(round(point.y * (frame_height - 1))),
            )

        return to_pixel(self.config.start), to_pixel(self.config.end)

    def _stable_side(self, point: NormalizedPoint) -> int | None:
        signed_distance = self._signed_distance(point)
        if abs(signed_distance) <= self.config.dead_zone:
            return None
        return SIDE_A if signed_distance > 0 else SIDE_B

    def _signed_distance(self, point: NormalizedPoint) -> float:
        start = self.config.start
        end = self.config.end
        cross_product = (
            (end.x - start.x) * (point.y - start.y)
            - (end.y - start.y) * (point.x - start.x)
        )
        return cross_product / self._line_length

    def _segment_intersection(
        self,
        movement_start: NormalizedPoint,
        movement_end: NormalizedPoint,
    ) -> NormalizedPoint | None:
        """Intersect vehicle movement with the finite configured gate line."""

        line_start = self.config.start
        line_end = self.config.end
        move_x = movement_end.x - movement_start.x
        move_y = movement_end.y - movement_start.y
        line_x = line_end.x - line_start.x
        line_y = line_end.y - line_start.y
        denominator = self._cross(move_x, move_y, line_x, line_y)

        if abs(denominator) <= 1e-12:
            return None

        offset_x = line_start.x - movement_start.x
        offset_y = line_start.y - movement_start.y
        movement_parameter = (
            self._cross(offset_x, offset_y, line_x, line_y) / denominator
        )
        line_parameter = (
            self._cross(offset_x, offset_y, move_x, move_y) / denominator
        )

        margin = self.config.segment_margin
        if not 0.0 <= movement_parameter <= 1.0:
            return None
        if not -margin <= line_parameter <= 1.0 + margin:
            return None

        intersection_x = movement_start.x + movement_parameter * move_x
        intersection_y = movement_start.y + movement_parameter * move_y
        return NormalizedPoint(
            min(1.0, max(0.0, intersection_x)),
            min(1.0, max(0.0, intersection_y)),
        )

    def _prune_stale(self, frame_index: int) -> None:
        cutoff = frame_index - self.config.max_idle_frames
        stale_ids = [
            track_id
            for track_id, state in self._tracks.items()
            if state.last_seen_frame < cutoff
        ]
        for track_id in stale_ids:
            del self._tracks[track_id]
        self._stale_tracks_removed += len(stale_ids)

    @staticmethod
    def _normalise_center(
        center: tuple[int | float, int | float],
        frame_width: int,
        frame_height: int,
    ) -> NormalizedPoint:
        x = min(1.0, max(0.0, float(center[0]) / (frame_width - 1)))
        y = min(1.0, max(0.0, float(center[1]) / (frame_height - 1)))
        return NormalizedPoint(x, y)

    @staticmethod
    def _distance(first: NormalizedPoint, second: NormalizedPoint) -> float:
        return math.hypot(second.x - first.x, second.y - first.y)

    @staticmethod
    def _cross(ax: float, ay: float, bx: float, by: float) -> float:
        return ax * by - ay * bx

    @staticmethod
    def _validate_update(
        *,
        track_id: int,
        center: tuple[int | float, int | float],
        frame_width: int,
        frame_height: int,
        frame_index: int,
    ) -> None:
        if isinstance(track_id, bool) or not isinstance(track_id, int):
            raise TypeError("track_id must be an integer")
        if track_id < 0:
            raise ValueError("track_id cannot be negative")
        if not isinstance(frame_index, int) or isinstance(frame_index, bool):
            raise TypeError("frame_index must be an integer")
        if frame_index < 0:
            raise ValueError("frame_index cannot be negative")
        if frame_width < 2 or frame_height < 2:
            raise ValueError("frame dimensions must be at least 2 by 2")
        if not isinstance(center, (tuple, list)) or len(center) != 2:
            raise TypeError("center must contain x and y coordinates")
        if not all(math.isfinite(float(value)) for value in center):
            raise ValueError("center coordinates must be finite")