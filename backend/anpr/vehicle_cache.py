"""Thread-safe in-memory cache of registered campus vehicles.

The cache stores immutable snapshots, including department data, so lookups do
not accidentally trigger lazy Django ORM queries.  Refresh builds a complete
replacement mapping and swaps it atomically only after a successful load.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from django.utils import timezone

from anpr.plate_validation import clean_plate_text
from vehicles.models import Vehicle


class VehicleCacheError(RuntimeError):
    """Raised when cache data cannot be built safely."""


@dataclass(frozen=True, slots=True)
class CachedVehicle:
    id: int
    registration_number: str
    owner_name: str
    owner_email: str
    owner_phone: str
    owner_type: str
    department_id: int | None
    department_code: str
    department_name: str
    vehicle_company: str
    vehicle_model: str
    vehicle_type: str
    color: str
    fuel_type: str
    registration_date: date
    valid_from: date
    valid_until: date
    authorization_status: str
    vehicle_image_name: str
    updated_at: datetime

    def effective_status(self, on_date: date | None = None) -> str:
        check_date = on_date or timezone.localdate()
        if check_date < self.valid_from:
            return Vehicle.AuthorizationStatus.PENDING
        if self.valid_until < check_date:
            return Vehicle.AuthorizationStatus.EXPIRED
        return self.authorization_status

    def is_authorized_on(self, on_date: date | None = None) -> bool:
        return (
            self.effective_status(on_date)
            == Vehicle.AuthorizationStatus.AUTHORIZED
        )


@dataclass(frozen=True, slots=True)
class VehicleLookupResult:
    plate_text: str
    found: bool
    authorized: bool
    authorization_status: str
    vehicle: CachedVehicle | None


@dataclass(frozen=True, slots=True)
class VehicleCacheStats:
    loaded: bool
    vehicle_count: int
    version: int
    refreshes: int
    updates: int
    removals: int
    hits: int
    misses: int
    loaded_at: datetime | None
    last_error: str


class VehicleCache:
    """Copy-on-write cache keyed by normalized registration number."""

    UNKNOWN_STATUS = "UNKNOWN"

    def __init__(self) -> None:
        self._vehicles: dict[str, CachedVehicle] = {}
        self._plate_by_id: dict[int, str] = {}
        self._lock = threading.RLock()
        self._loaded = False
        self._version = 0
        self._refreshes = 0
        self._updates = 0
        self._removals = 0
        self._hits = 0
        self._misses = 0
        self._loaded_at: datetime | None = None
        self._last_error = ""

    def refresh(
        self,
        vehicles: Iterable[Vehicle] | None = None,
    ) -> int:
        """Load all vehicles and atomically replace the current cache."""

        try:
            source = (
                vehicles
                if vehicles is not None
                else Vehicle.objects.select_related("department").all()
            )
            replacement: dict[str, CachedVehicle] = {}
            replacement_ids: dict[int, str] = {}

            for vehicle in source:
                snapshot = self._snapshot(vehicle)
                existing = replacement.get(snapshot.registration_number)
                if existing is not None and existing.id != snapshot.id:
                    raise VehicleCacheError(
                        "Duplicate normalized registration number "
                        f"'{snapshot.registration_number}' was found."
                    )
                replacement[snapshot.registration_number] = snapshot
                replacement_ids[snapshot.id] = snapshot.registration_number
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            raise

        with self._lock:
            self._vehicles = replacement
            self._plate_by_id = replacement_ids
            self._loaded = True
            self._version += 1
            self._refreshes += 1
            self._loaded_at = timezone.now()
            self._last_error = ""
            return len(replacement)

    def ensure_loaded(self) -> int:
        """Load once when a service forgot to warm the cache explicitly."""

        with self._lock:
            if self._loaded:
                return len(self._vehicles)
        return self.refresh()

    def lookup(self, plate_text: str) -> CachedVehicle | None:
        normalized = self.normalize_plate(plate_text)
        with self._lock:
            vehicle = self._vehicles.get(normalized)
            if vehicle is None:
                self._misses += 1
            else:
                self._hits += 1
            return vehicle

    def lookup_result(
        self,
        plate_text: str,
        *,
        on_date: date | None = None,
    ) -> VehicleLookupResult:
        normalized = self.normalize_plate(plate_text)
        vehicle = self.lookup(normalized)
        if vehicle is None:
            return VehicleLookupResult(
                plate_text=normalized,
                found=False,
                authorized=False,
                authorization_status=self.UNKNOWN_STATUS,
                vehicle=None,
            )

        status = vehicle.effective_status(on_date)
        return VehicleLookupResult(
            plate_text=normalized,
            found=True,
            authorized=(status == Vehicle.AuthorizationStatus.AUTHORIZED),
            authorization_status=status,
            vehicle=vehicle,
        )

    def upsert(self, vehicle: Vehicle) -> CachedVehicle:
        """Insert or replace one vehicle after a committed database change."""

        snapshot = self._snapshot(vehicle)
        with self._lock:
            replacement = dict(self._vehicles)
            replacement_ids = dict(self._plate_by_id)

            previous_plate = replacement_ids.get(snapshot.id)
            if previous_plate and previous_plate != snapshot.registration_number:
                replacement.pop(previous_plate, None)

            conflicting = replacement.get(snapshot.registration_number)
            if conflicting is not None and conflicting.id != snapshot.id:
                raise VehicleCacheError(
                    "Cannot cache duplicate normalized registration number "
                    f"'{snapshot.registration_number}'."
                )

            replacement[snapshot.registration_number] = snapshot
            replacement_ids[snapshot.id] = snapshot.registration_number
            self._vehicles = replacement
            self._plate_by_id = replacement_ids
            self._loaded = True
            self._version += 1
            self._updates += 1
            self._last_error = ""
            return snapshot

    def remove(
        self,
        *,
        vehicle_id: int | None = None,
        registration_number: str | None = None,
    ) -> bool:
        if vehicle_id is None and registration_number is None:
            raise ValueError(
                "vehicle_id or registration_number must be provided"
            )

        normalized = (
            self.normalize_plate(registration_number)
            if registration_number is not None
            else None
        )

        with self._lock:
            plate = normalized
            if vehicle_id is not None:
                plate = self._plate_by_id.get(int(vehicle_id), plate)
            if not plate or plate not in self._vehicles:
                return False

            snapshot = self._vehicles[plate]
            replacement = dict(self._vehicles)
            replacement_ids = dict(self._plate_by_id)
            del replacement[plate]
            replacement_ids.pop(snapshot.id, None)
            self._vehicles = replacement
            self._plate_by_id = replacement_ids
            self._version += 1
            self._removals += 1
            return True

    def clear(self) -> None:
        with self._lock:
            self._vehicles = {}
            self._plate_by_id = {}
            self._loaded = False
            self._version += 1
            self._loaded_at = None

    def stats(self) -> VehicleCacheStats:
        with self._lock:
            return VehicleCacheStats(
                loaded=self._loaded,
                vehicle_count=len(self._vehicles),
                version=self._version,
                refreshes=self._refreshes,
                updates=self._updates,
                removals=self._removals,
                hits=self._hits,
                misses=self._misses,
                loaded_at=self._loaded_at,
                last_error=self._last_error,
            )

    @staticmethod
    def normalize_plate(plate_text: str | None) -> str:
        if plate_text is None:
            return ""
        return clean_plate_text(str(plate_text))

    @staticmethod
    def _snapshot(vehicle: Vehicle) -> CachedVehicle:
        registration_number = VehicleCache.normalize_plate(
            vehicle.registration_number
        )
        if not registration_number:
            raise VehicleCacheError(
                f"Vehicle {vehicle.pk!r} has an empty registration number."
            )

        department = vehicle.department
        department_id = vehicle.department_id
        department_code = department.name if department is not None else ""
        department_name = (
            department.get_name_display() if department is not None else ""
        )
        image_name = (
            vehicle.vehicle_image.name if vehicle.vehicle_image else ""
        )

        return CachedVehicle(
            id=int(vehicle.pk),
            registration_number=registration_number,
            owner_name=vehicle.owner_name,
            owner_email=vehicle.owner_email or "",
            owner_phone=vehicle.owner_phone or "",
            owner_type=vehicle.owner_type,
            department_id=department_id,
            department_code=department_code,
            department_name=department_name,
            vehicle_company=vehicle.vehicle_company,
            vehicle_model=vehicle.vehicle_model,
            vehicle_type=vehicle.vehicle_type,
            color=vehicle.color,
            fuel_type=vehicle.fuel_type,
            registration_date=vehicle.registration_date,
            valid_from=vehicle.valid_from,
            valid_until=vehicle.valid_until,
            authorization_status=vehicle.authorization_status,
            vehicle_image_name=image_name,
            updated_at=vehicle.updated_at,
        )


vehicle_cache = VehicleCache()


def get_vehicle_cache() -> VehicleCache:
    return vehicle_cache