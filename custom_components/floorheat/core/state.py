"""Logic state of the control core and its persistence format (docs/design.md §3.8).

The adapter stores `CoreState.to_dict()` with `helpers.storage.Store` and restores it
with `load_state`. Format rules (D-87):
- zones are keyed by the stable zone id (D-76);
- datetimes are ISO 8601 in UTC, dates ISO 8601;
- new fields get a default when missing, so adding one needs no version bump;
- a breaking change bumps `SCHEMA_VERSION` and adds a migration in `from_dict`;
- data from a newer version, or corrupt data, is discarded and the core starts as on a
  first start (D-78).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from .config import CoreConfig

SCHEMA_VERSION = 1


class StateFormatError(ValueError):
    """Persisted state that cannot be read."""


class ZoneMode(StrEnum):
    """Zone state (§3.2)."""

    IDLE = "idle"
    WAITING = "waiting"
    HEATING = "heating"
    FORCED = "forced"
    SENSOR_FAULT = "sensor_fault"


@dataclass(frozen=True)
class OutputTracking:
    """Consecutive reconcile ticks an output did not follow its command, whether that
    was notified, and the desired state at the last tick (D-67, D-99)."""

    mismatch_count: int = 0
    alerted: bool = False
    last_desired: bool | None = None


@dataclass(frozen=True)
class ZoneState:
    """Per-zone logic state."""

    mode: ZoneMode = ZoneMode.IDLE
    wait_started_at: datetime | None = None  # WaitTime start (§3.3 rule 1)
    last_valid_value: float | None = None  # raw reading, °C, before the offset
    last_valid_at: datetime | None = None  # `last_reported` of that reading (§3.6)
    fault_since: datetime | None = None  # SENSOR_FAULT start (§3.6)
    forced_capped: bool = False  # FORCED zone at or above ManualMaxTemp (§3.4)
    valve_output: OutputTracking = OutputTracking()
    last_setpoint: float | None = None  # effective SetPoint of the last step (rule 4)
    awaiting_reading_since: datetime | None = None  # no valid reading ever yet (D-93)


@dataclass(frozen=True)
class CoreState:
    """Global logic state (§3.8). Alerts are derived from these fields."""

    zones: dict[str, ZoneState] = field(default_factory=dict)
    hp_actual_on: bool | None = None  # last known actual switch state; None = never seen
    hp_last_on_at: datetime | None = None  # actual transitions (D-66)
    hp_last_off_at: datetime | None = None  # None: first start, no min OFF (D-78)
    hp_unavailable_since: datetime | None = None  # switch unavailable since (D-95)
    calling_zone: str | None = None
    sync_fired: bool = False
    heat_source_output: OutputTracking = OutputTracking()
    last_fault_reminder_on: date | None = None  # local date of the last daily reminder
    reconcile_tick_at: datetime | None = None  # `now` of the last counted tick (D-99)

    @classmethod
    def initial(cls, config: CoreConfig) -> CoreState:
        """State of a first start without persisted data."""
        return cls(zones={zone_id: ZoneState() for zone_id in config.zone_ids})

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, tagged with `SCHEMA_VERSION`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "zones": {zone_id: _zone_to_dict(zone) for zone_id, zone in self.zones.items()},
            "hp_actual_on": self.hp_actual_on,
            "hp_last_on_at": _dt_to_str(self.hp_last_on_at),
            "hp_last_off_at": _dt_to_str(self.hp_last_off_at),
            "hp_unavailable_since": _dt_to_str(self.hp_unavailable_since),
            "calling_zone": self.calling_zone,
            "sync_fired": self.sync_fired,
            "heat_source_output": _tracking_to_dict(self.heat_source_output),
            "last_fault_reminder_on": (
                None
                if self.last_fault_reminder_on is None
                else self.last_fault_reminder_on.isoformat()
            ),
            "reconcile_tick_at": _dt_to_str(self.reconcile_tick_at),
        }

    @classmethod
    def from_dict(cls, data: object) -> CoreState:
        """Parse `to_dict` output. Raises `StateFormatError` if it cannot be used."""
        reader = _Reader(data, "")
        version = reader.required_int("schema_version")
        if version > SCHEMA_VERSION:
            raise StateFormatError(
                f"schema version {version} is newer than this version of floorheat "
                f"supports ({SCHEMA_VERSION})"
            )
        if version != SCHEMA_VERSION:
            raise StateFormatError(f"unsupported schema version {version}")
        zones = reader.child("zones")
        return cls(
            zones={}
            if zones is None
            else {z: _zone_from(zones.child_at(z)) for z in zones.child_keys()},
            hp_actual_on=reader.opt_bool("hp_actual_on"),
            hp_last_on_at=reader.opt_datetime("hp_last_on_at"),
            hp_last_off_at=reader.opt_datetime("hp_last_off_at"),
            hp_unavailable_since=reader.opt_datetime("hp_unavailable_since"),
            calling_zone=reader.opt_str("calling_zone"),
            sync_fired=reader.boolean("sync_fired", default=False),
            heat_source_output=_tracking_from(reader.child("heat_source_output")),
            last_fault_reminder_on=reader.opt_date("last_fault_reminder_on"),
            reconcile_tick_at=reader.opt_datetime("reconcile_tick_at"),
        )


def load_state(data: object, config: CoreConfig) -> tuple[CoreState, list[str]]:
    """Restore persisted state for `config`; returns the state and warnings to log.

    `data` is None when nothing was stored (a real first start). Unusable data falls
    back to a first start (D-87). Zones no longer configured are dropped, new zones
    start `IDLE`.
    """
    initial = CoreState.initial(config)
    if data is None:
        return initial, []
    try:
        stored = CoreState.from_dict(data)
    except StateFormatError as err:
        return initial, [
            f"Stored floorheat state is unusable ({err}); starting as on a first start."
        ]
    warnings: list[str] = []
    dropped = sorted(set(stored.zones) - set(config.zone_ids))
    if dropped:
        warnings.append(
            f"Discarded stored state of zones no longer configured: {', '.join(dropped)}"
        )
    calling = stored.calling_zone if stored.calling_zone in config.zone_ids else None
    state = dataclasses.replace(
        stored,
        zones={z: stored.zones.get(z, ZoneState()) for z in config.zone_ids},
        calling_zone=calling,
    )
    return state, warnings


# ---------------------------------------------------------------- serialisation helpers


def _dt_to_str(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _tracking_to_dict(tracking: OutputTracking) -> dict[str, Any]:
    return {
        "mismatch_count": tracking.mismatch_count,
        "alerted": tracking.alerted,
        "last_desired": tracking.last_desired,
    }


def _zone_to_dict(zone: ZoneState) -> dict[str, Any]:
    return {
        "mode": zone.mode.value,
        "wait_started_at": _dt_to_str(zone.wait_started_at),
        "last_valid_value": zone.last_valid_value,
        "last_valid_at": _dt_to_str(zone.last_valid_at),
        "fault_since": _dt_to_str(zone.fault_since),
        "forced_capped": zone.forced_capped,
        "valve_output": _tracking_to_dict(zone.valve_output),
        "last_setpoint": zone.last_setpoint,
        "awaiting_reading_since": _dt_to_str(zone.awaiting_reading_since),
    }


def _tracking_from(reader: _Reader | None) -> OutputTracking:
    if reader is None:
        return OutputTracking()
    return OutputTracking(
        mismatch_count=reader.integer("mismatch_count", default=0, minimum=0),
        alerted=reader.boolean("alerted", default=False),
        last_desired=reader.opt_bool("last_desired"),
    )


def _zone_from(reader: _Reader) -> ZoneState:
    return ZoneState(
        mode=reader.mode("mode"),
        wait_started_at=reader.opt_datetime("wait_started_at"),
        last_valid_value=reader.opt_float("last_valid_value"),
        last_valid_at=reader.opt_datetime("last_valid_at"),
        fault_since=reader.opt_datetime("fault_since"),
        forced_capped=reader.boolean("forced_capped", default=False),
        valve_output=_tracking_from(reader.child("valve_output")),
        last_setpoint=reader.opt_float("last_setpoint"),
        awaiting_reading_since=reader.opt_datetime("awaiting_reading_since"),
    )


_MISSING = object()


class _Reader:
    """Typed access to one mapping of persisted data, with error paths like `zones.a.mode`."""

    def __init__(self, data: object, path: str) -> None:
        if not isinstance(data, Mapping):
            raise StateFormatError(f"{path or 'state'}: expected a mapping, got {data!r}")
        self._data: Mapping[object, object] = data
        self._path = path

    def _where(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def _fail(self, key: str, expected: str, value: object) -> StateFormatError:
        return StateFormatError(f"{self._where(key)}: expected {expected}, got {value!r}")

    def child_keys(self) -> list[str]:
        keys = list(self._data)
        for key in keys:
            if not isinstance(key, str):
                raise StateFormatError(f"{self._path}: keys must be strings, got {key!r}")
        return [str(key) for key in keys]

    def child_at(self, key: str) -> _Reader:
        return _Reader(self._data[key], self._where(key))

    def child(self, key: str) -> _Reader | None:
        value = self._data.get(key, _MISSING)
        return None if value is _MISSING else _Reader(value, self._where(key))

    def required_int(self, key: str) -> int:
        value = self._data.get(key, _MISSING)
        if value is _MISSING:
            raise StateFormatError(f"{self._where(key)}: missing")
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._fail(key, "an integer", value)
        return value

    def integer(self, key: str, *, default: int, minimum: int) -> int:
        value = self._data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise self._fail(key, f"an integer ≥ {minimum}", value)
        return value

    def boolean(self, key: str, *, default: bool) -> bool:
        value = self._data.get(key, default)
        if not isinstance(value, bool):
            raise self._fail(key, "true or false", value)
        return value

    def opt_bool(self, key: str) -> bool | None:
        value = self._data.get(key)
        if value is not None and not isinstance(value, bool):
            raise self._fail(key, "true, false or null", value)
        return value

    def opt_str(self, key: str) -> str | None:
        value = self._data.get(key)
        if value is not None and not isinstance(value, str):
            raise self._fail(key, "a string or null", value)
        return value

    def opt_float(self, key: str) -> float | None:
        value = self._data.get(key)
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise self._fail(key, "a finite number or null", value)
        return float(value)

    def opt_datetime(self, key: str) -> datetime | None:
        value = self._data.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._fail(key, "an ISO 8601 datetime or null", value)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise self._fail(key, "an ISO 8601 datetime or null", value) from None
        if parsed.utcoffset() is None:
            raise StateFormatError(f"{self._where(key)}: datetime {value!r} has no time zone")
        return parsed

    def opt_date(self, key: str) -> date | None:
        value = self._data.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._fail(key, "an ISO 8601 date or null", value)
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise self._fail(key, "an ISO 8601 date or null", value) from None

    def mode(self, key: str) -> ZoneMode:
        value = self._data.get(key, ZoneMode.IDLE.value)
        if not isinstance(value, str) or value not in ZoneMode:
            raise self._fail(key, f"one of {', '.join(ZoneMode)}", value)
        return ZoneMode(value)
