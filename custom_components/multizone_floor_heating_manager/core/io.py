"""Inputs, outputs and events of the step function.

`step(config, state, inputs, now) -> (outputs, new_state, events)`. All
temperatures are °C; the adapter converts from HA's unit system.
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

    In shadow mode the adapter passes the commanded state instead.
    """

    ON = "on"
    OFF = "off"
    UNAVAILABLE = "unavailable"

    @property
    def is_on(self) -> bool:
        """Unavailable counts as OFF."""
        return self is OutputState.ON


@dataclass(frozen=True)
class ZoneInput:
    """What the adapter observed for one zone."""

    reading: float | None  # raw sensor value, °C, before the offset; None if not numeric
    last_reported: datetime | None  # the sensor's `last_reported`
    valve: OutputState | None  # None for a zone without a valve


@dataclass(frozen=True)
class Inputs:
    """Everything `step` reads besides config, state and time.

    Keyed by zone id; every valved zone carries its valve state.

    - `time_zone`: HA's configured time zone. `now` may be in any time zone; the core
      converts it for local wall-clock rules.
    - `shadow_mode`: True = shadow mode, the adapter sends no commands and passes the
      commanded states as `heat_source` and valve states; False = the integration
      controls the switches.
    - `reconcile_tick`: True only for the run started by the ReconcileInterval timer, not
      for runs on sensor updates or heat source changes. The mismatch counter counts
      these ticks.
    - `schedules`, `holiday_on` and `holiday_until`: owned and stored by the adapter. Holiday is
      active while it is switched on and `now` is before its end; without an end it runs until
      switched off."""

    zones: Mapping[str, ZoneInput]
    heat_source: OutputState
    zone_params: Mapping[str, ZoneParams]
    global_params: GlobalParams
    heating_season: bool
    shadow_mode: bool
    time_zone: tzinfo
    reconcile_tick: bool
    schedules: Sequence[Schedule] = ()
    holiday_on: bool = False
    holiday_until: datetime | None = None  # the holiday end; None: no end


class Reason(StrEnum):
    """Why a zone is in its state: fixed keys, never countdowns.

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
    FORCED = "forced"  # manual schedule
    FORCED_TOO_WARM = "forced_too_warm"  # manual schedule paused by ManualMaxTemp
    FAILSAFE_HEATING = "failsafe_heating"  # failsafe window
    FAILSAFE_WAITING = "failsafe_waiting"  # failsafe, before the next window
    VALVE_EXERCISE = "valve_exercise"  # off season: this valve is exercised


class HeatSourceStatus(StrEnum):
    """What the heat source does and why: fixed keys like `Reason`; the end of a
    running min ON/OFF timer is reported separately. The first that applies wins."""

    UNAVAILABLE = "unavailable"  # the switch does not report
    SEASON_OFF = "season_off"
    HELD_BY_MIN_OFF = "held_by_minimum_off_time"  # demand waits for HpMinOffTime
    SPREADING_HEAT = "spreading_heat"  # no demand, running until HpMinOnTime
    FAILSAFE_HEATING = "failsafe_heating"  # failsafe window
    FAILSAFE_WAITING = "failsafe_waiting"  # failsafe, before the next window
    HEATING = "heating"
    IDLE = "idle"  # no demand


class Mode(StrEnum):
    """The mode sensor: failsafe before holiday."""

    NORMAL = "normal"
    HOLIDAY = "holiday"
    FAILSAFE = "failsafe"


@dataclass(frozen=True)
class ZoneReport:
    """What the adapter shows for one zone."""

    reason: Reason
    room_temp: float | None  # RoomTemp, °C; None while unknown or faulty
    setpoint: float  # effective SetPoint, °C
    until: datetime | None = None  # end of the timer the reason names (wait, min ON/OFF,
    # manual window)


@dataclass(frozen=True)
class Outputs:
    """Desired output states; `valves` has entries for valved zones only.

    `holiday_active` and `ended_schedules` (one-shot schedules whose window is over) tell
    the adapter to switch holiday off and delete those schedules.
    `heat_source_status` / `heat_source_until`: the heat source sensor.
    `mode`: normal / holiday / failsafe. `valve_exercise`: the zone whose valve
    is being exercised, if any.
    """

    heat_source_on: bool
    valves: Mapping[str, bool]
    zones: Mapping[str, ZoneReport] = field(default_factory=dict)
    holiday_active: bool = False
    ended_schedules: tuple[str, ...] = ()
    heat_source_status: HeatSourceStatus = HeatSourceStatus.IDLE
    heat_source_until: datetime | None = None  # end of the timer it names
    mode: Mode = Mode.NORMAL
    valve_exercise: str | None = None


class EventKind(StrEnum):
    """Notifications from the core."""

    SENSOR_FAULT_STARTED = "sensor_fault_started"
    SENSOR_FAULT_REMINDER = "sensor_fault_reminder"
    SENSOR_FAULT_RECOVERED = "sensor_fault_recovered"
    OUTPUT_MISMATCH = "output_mismatch"
    OUTPUT_MISMATCH_RECOVERED = "output_mismatch_recovered"
    WATCHDOG_FAILED = "watchdog_failed"  # Shelly unreachable / script not running
    WATCHDOG_RECOVERED = "watchdog_recovered"
    WATCHDOG_PARAMS_MISMATCH = "watchdog_params_mismatch"
    FAILSAFE_STARTED = "failsafe_started"  # every sensor dead, HA running
    FAILSAFE_ENDED = "failsafe_ended"
    LONG_RUN = "long_run"  # heat source ON longer than LongRunAlarm
    LONG_RUN_ENDED = "long_run_ended"


@dataclass(frozen=True)
class Event:
    """Something the adapter turns into a notification or log entry."""

    kind: EventKind
    message: str
    zone_id: str | None = None
    data: Mapping[str, str | float | bool | None] = field(default_factory=dict)
