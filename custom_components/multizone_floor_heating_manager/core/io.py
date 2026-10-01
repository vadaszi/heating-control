"""Inputs, outputs and events of the step function (docs/design.md §5.3).

`step(config, state, inputs, now) -> (outputs, new_state, events)` (P2). All
temperatures are °C; the adapter converts from HA's unit system (D-77).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from enum import StrEnum

from .config import GlobalParams, ZoneParams
from .schedule import Schedule


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

    Keyed by zone id (D-76); every valved zone carries its valve state.

    - `time_zone`: HA's configured time zone. `now` may be in any time zone; the core
      converts it for local wall-clock rules (D-96).
    - `reconcile_tick`: True only for the run started by the ReconcileInterval timer, not
      for runs on sensor updates or heat source changes. The mismatch counter counts
      these ticks (D-67, D-99).
    - `schedules`, `holiday_on` and `holiday_until`: owned and stored by the adapter
      (D-136). Holiday is active while it is switched on and `now` is before its end;
      without an end it runs until switched off (D-137).
    """

    zones: Mapping[str, ZoneInput]
    heat_source: OutputState
    zone_params: Mapping[str, ZoneParams]
    global_params: GlobalParams
    heating_season: bool
    control_active: bool
    time_zone: tzinfo
    reconcile_tick: bool
    schedules: Sequence[Schedule] = ()
    holiday_on: bool = False
    holiday_until: datetime | None = None  # the holiday end; None: no end (D-137)


class Reason(StrEnum):
    """Why a zone is in its state (D-89, D-126): fixed keys, never countdowns (D-123).

    The adapter shows them as translated texts; history and automations see the key.
    """

    IDLE = "idle"
    WAITING = "waiting"
    CALLING_ZONE = "calling_zone"
    HEATING = "heating"
    HELD_BY_MIN_OFF = "held_by_minimum_off_time"
    SPREADING_HEAT = "spreading_heat"
    TOO_WARM_FOR_SPREADING = "too_warm_for_spreading"
    HEAT_SOURCE_UNAVAILABLE = "heat_source_unavailable"
    NO_READING_YET = "no_reading_yet"
    SENSOR_FAULT = "sensor_fault"
    SEASON_OFF = "season_off"
    SENSOR_FAULT_SEASON_OFF = "sensor_fault_season_off"
    FORCED = "forced"  # manual schedule (D-135)
    FORCED_TOO_WARM = "forced_too_warm"  # manual schedule paused by ManualMaxTemp (D-38)


class HeatSourceStatus(StrEnum):
    """What the heat source does and why (D-141): fixed keys like `Reason`; the end of a
    running min ON/OFF timer is reported separately. The first that applies wins."""

    UNAVAILABLE = "unavailable"  # the switch does not report
    SEASON_OFF = "season_off"
    HELD_BY_MIN_OFF = "held_by_minimum_off_time"  # demand waits for HpMinOffTime (D-64)
    SPREADING_HEAT = "spreading_heat"  # no demand, running until HpMinOnTime (D-20)
    HEATING = "heating"
    IDLE = "idle"  # no demand


@dataclass(frozen=True)
class ZoneReport:
    """What the adapter shows for one zone (D-89, D-123)."""

    reason: Reason
    room_temp: float | None  # RoomTemp, °C; None while unknown or faulty
    setpoint: float  # effective SetPoint, °C
    until: datetime | None = None  # end of the timer the reason names (wait, min ON/OFF,
    # manual window)


@dataclass(frozen=True)
class Outputs:
    """Desired output states; `valves` has entries for valved zones only.

    `holiday_active` and `ended_schedules` (one-shot schedules whose window is over) tell
    the adapter to switch holiday off and delete those schedules (D-136).
    `heat_source_status` / `heat_source_until`: the heat source sensor (D-141).
    """

    heat_source_on: bool
    valves: Mapping[str, bool]
    zones: Mapping[str, ZoneReport] = field(default_factory=dict)
    holiday_active: bool = False
    ended_schedules: tuple[str, ...] = ()
    heat_source_status: HeatSourceStatus = HeatSourceStatus.IDLE
    heat_source_until: datetime | None = None  # end of the min OFF/ON timer it names


class EventKind(StrEnum):
    """Notifications from the core (§3.6 table); later phases add their kinds."""

    SENSOR_FAULT_STARTED = "sensor_fault_started"
    SENSOR_FAULT_REMINDER = "sensor_fault_reminder"
    SENSOR_FAULT_RECOVERED = "sensor_fault_recovered"
    OUTPUT_MISMATCH = "output_mismatch"
    OUTPUT_MISMATCH_RECOVERED = "output_mismatch_recovered"
    WATCHDOG_FAILED = "watchdog_failed"  # Shelly unreachable / script not running (D-61)
    WATCHDOG_RECOVERED = "watchdog_recovered"
    WATCHDOG_PARAMS_MISMATCH = "watchdog_params_mismatch"  # D-73


@dataclass(frozen=True)
class Event:
    """Something the adapter turns into a notification or log entry."""

    kind: EventKind
    message: str
    zone_id: str | None = None
    data: Mapping[str, str | float | bool | None] = field(default_factory=dict)
