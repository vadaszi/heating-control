"""Inputs, outputs and events of the step function (docs/design.md §5.3).

`step(config, state, inputs, now) -> (outputs, new_state, events)` (P2). All
temperatures are °C; the adapter converts from HA's unit system (D-77).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .config import GlobalParams, ZoneParams


class OutputState(StrEnum):
    """Actual state of a switch as reported by HA.

    In shadow mode the adapter passes the commanded state instead (D-66).
    """

    ON = "on"
    OFF = "off"
    UNAVAILABLE = "unavailable"

    @property
    def is_on(self) -> bool:
        """Unavailable counts as OFF (D-66)."""
        return self is OutputState.ON


@dataclass(frozen=True)
class ZoneInput:
    """What the adapter observed for one zone."""

    reading: float | None  # raw sensor value, °C, before the offset; None if not numeric
    last_reported: datetime | None  # the sensor's `last_reported` (§5.3)
    valve: OutputState | None  # None for a zone without a valve


@dataclass(frozen=True)
class Inputs:
    """Everything `step` reads besides config, state and time.

    Keyed by zone id (D-76). Schedules and holiday are added in P9.
    """

    zones: Mapping[str, ZoneInput]
    heat_source: OutputState
    zone_params: Mapping[str, ZoneParams]
    global_params: GlobalParams
    heating_season: bool
    control_active: bool


@dataclass(frozen=True)
class ZoneReport:
    """What the adapter shows for one zone (D-89)."""

    reason: str  # e.g. "Calling zone", "Waiting, 12 min left"
    room_temp: float | None  # RoomTemp, °C; None while unknown or faulty
    setpoint: float  # effective SetPoint, °C


@dataclass(frozen=True)
class Outputs:
    """Desired output states; `valves` has entries for valved zones only."""

    heat_source_on: bool
    valves: Mapping[str, bool]
    zones: Mapping[str, ZoneReport] = field(default_factory=dict)


class EventKind(StrEnum):
    """Notifications from the core (§3.6 table); later phases add their kinds."""

    SENSOR_FAULT_STARTED = "sensor_fault_started"
    SENSOR_FAULT_REMINDER = "sensor_fault_reminder"
    SENSOR_FAULT_RECOVERED = "sensor_fault_recovered"
    OUTPUT_MISMATCH = "output_mismatch"
    OUTPUT_MISMATCH_RECOVERED = "output_mismatch_recovered"


@dataclass(frozen=True)
class Event:
    """Something the adapter turns into a notification or log entry."""

    kind: EventKind
    message: str
    zone_id: str | None = None
    data: Mapping[str, str | float | bool | None] = field(default_factory=dict)
