"""Configuration and parameter models with §4 defaults and ranges (docs/design.md §4, §5.6).

- `ZoneConfig` / `CoreConfig`: the wiring from YAML (zones, *config* values).
- `ZoneParams` / `GlobalParams`: the values the user changes from the UI; they reach
  `step` through `Inputs`.
- `PARAM_SPECS`: every §4 default, range and step. The adapter builds its YAML schema
  and number entities from it; tests keep the dataclass defaults equal to it.

Every model validates itself on construction and reports all problems at once, so an
invalid configuration never reaches the control logic. Only the inclusive range is
checked; the §4 step is UI granularity (D-86).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import TypeIs

# Absorbs float noise from unit conversion at a range boundary (e.g. 0.1 °C via °F).
_TOLERANCE = 1e-9

_ZONE_ID = re.compile(r"[a-z][a-z0-9_]*")


class ConfigError(ValueError):
    """Invalid configuration or parameter values; `errors` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(
            "Invalid Multizone Floor Heating Manager configuration:\n"
            + "\n".join(f"- {e}" for e in self.errors)
        )


class ParamUnit(StrEnum):
    """Unit of a §4 parameter as shown to the user (°C, before adapter conversion, D-77)."""

    CELSIUS = "celsius"
    CELSIUS_DELTA = "celsius_delta"
    MINUTES = "minutes"
    HOURS = "hours"

    @property
    def symbol(self) -> str:
        return _SYMBOLS[self]

    @property
    def is_temperature(self) -> bool:
        return self in (ParamUnit.CELSIUS, ParamUnit.CELSIUS_DELTA)


_SYMBOLS = {
    ParamUnit.CELSIUS: "°C",
    ParamUnit.CELSIUS_DELTA: "°C",
    ParamUnit.MINUTES: "min",
    ParamUnit.HOURS: "h",
}
_DURATION_UNITS = {ParamUnit.MINUTES: timedelta(minutes=1), ParamUnit.HOURS: timedelta(hours=1)}


@dataclass(frozen=True)
class ParamSpec:
    """Default, range and step of one §4 parameter, in the units of `unit`.

    Temperatures are held as `float` °C; durations as `timedelta`.
    """

    key: str
    default: float
    minimum: float
    maximum: float
    step: float
    unit: ParamUnit

    def from_number(self, number: float) -> float | timedelta:
        """Model value for a number in this spec's unit (e.g. 30 min → timedelta)."""
        if self.unit.is_temperature:
            return float(number)
        return number * _DURATION_UNITS[self.unit]

    def to_number(self, value: float | timedelta) -> float:
        """Number in this spec's unit for a model value (inverse of `from_number`)."""
        if isinstance(value, timedelta):
            return value / _DURATION_UNITS[self.unit]
        return float(value)

    def check(self, value: object) -> str | None:
        """Return an error text if `value` is not a valid model value, else None."""
        if self.unit.is_temperature:
            if isinstance(value, bool) or not isinstance(value, int | float):
                return f"{self.key}: expected a number, got {value!r}"
            if not math.isfinite(value):
                return f"{self.key}: must be a finite number, got {value!r}"
        elif not isinstance(value, timedelta):
            return f"{self.key}: expected a duration, got {value!r}"
        number = self.to_number(value)
        if self.minimum - _TOLERANCE <= number <= self.maximum + _TOLERANCE:
            return None
        symbol = self.unit.symbol
        return (
            f"{self.key}: {number:g} {symbol} is out of range "
            f"({self.minimum:g} to {self.maximum:g} {symbol})"
        )


def _specs(*specs: ParamSpec) -> Mapping[str, ParamSpec]:
    return MappingProxyType({spec.key: spec for spec in specs})


_C, _DELTA, _MIN, _H = (
    ParamUnit.CELSIUS,
    ParamUnit.CELSIUS_DELTA,
    ParamUnit.MINUTES,
    ParamUnit.HOURS,
)

ZONE_PARAM_SPECS = _specs(
    ParamSpec("base_setpoint", 22.0, 10, 30, 0.1, _C),
    ParamSpec("hysteresis", 0.2, 0.1, 1.0, 0.1, _DELTA),
    ParamSpec("wait_time", 30, 0, 120, 5, _MIN),
    ParamSpec("holiday_temp", 18.0, 10, 25, 0.5, _C),  # per zone (D-133)
)
GLOBAL_PARAM_SPECS = _specs(
    ParamSpec("hp_min_on_time", 60, 30, 180, 5, _MIN),  # never below 30 min (D-81)
    ParamSpec("hp_min_off_time", 60, 30, 180, 5, _MIN),  # never below 30 min (D-81)
    ParamSpec("sensor_fault_timeout", 60, 15, 240, 5, _MIN),
    ParamSpec("manual_max_temp", 25.0, 18, 30, 0.5, _C),
    ParamSpec("manual_resume_delta", 1.0, 0.2, 3.0, 0.1, _DELTA),
    ParamSpec("failsafe_trigger", 24, 1, 72, 1, _H),
    ParamSpec("valve_exercise_duration", 15, 5, 30, 5, _MIN),
    ParamSpec("long_run_alarm", 12, 2, 48, 1, _H),
)
SENSOR_OFFSET_SPEC = ParamSpec("sensor_offset", 0.0, -5, 5, 0.1, _DELTA)
PARAM_SPECS = _specs(*ZONE_PARAM_SPECS.values(), *GLOBAL_PARAM_SPECS.values(), SENSOR_OFFSET_SPEC)


def _param_errors(obj: object, specs: Mapping[str, ParamSpec]) -> list[str]:
    return [error for key, spec in specs.items() if (error := spec.check(getattr(obj, key)))]


@dataclass(frozen=True)
class ZoneParams:
    """Per-zone values changed from the UI (§4). Defaults equal `ZONE_PARAM_SPECS`."""

    base_setpoint: float = 22.0
    hysteresis: float = 0.2
    wait_time: timedelta = timedelta(minutes=30)
    holiday_temp: float = 18.0  # effective SetPoint while holiday is active (D-133)

    def __post_init__(self) -> None:
        if errors := _param_errors(self, ZONE_PARAM_SPECS):
            raise ConfigError(errors)


# Global parameters that are a local time of day (§4), not a number.
TIME_OF_DAY_PARAMS = (
    "sensor_fault_reminder",
    "failsafe_window_start",
    "failsafe_window_end",
    "valve_exercise_time",
)


@dataclass(frozen=True)
class GlobalParams:
    """Global values changed from the UI (§4). HolidayTemp is per zone since D-133
    (`ZoneParams`).

    Times of day are local wall-clock times (`TIME_OF_DAY_PARAMS`). The failsafe window
    may cross midnight; start = end is rejected (D-147). `valve_exercise_weekday` is
    Monday = 0, as `date.weekday()` (D-149).
    """

    hp_min_on_time: timedelta = timedelta(minutes=60)
    hp_min_off_time: timedelta = timedelta(minutes=60)
    sensor_fault_timeout: timedelta = timedelta(minutes=60)
    sensor_fault_reminder: time = time(8, 0)
    manual_max_temp: float = 25.0
    manual_resume_delta: float = 1.0
    failsafe_trigger: timedelta = timedelta(hours=24)
    failsafe_window_start: time = time(10, 0)
    failsafe_window_end: time = time(15, 0)
    valve_exercise_weekday: int = 0
    valve_exercise_time: time = time(8, 0)
    valve_exercise_duration: timedelta = timedelta(minutes=15)
    long_run_alarm: timedelta = timedelta(hours=12)

    def __post_init__(self) -> None:
        errors = _param_errors(self, GLOBAL_PARAM_SPECS)
        for key in TIME_OF_DAY_PARAMS:
            value: object = getattr(self, key)
            if not isinstance(value, time):
                errors.append(f"{key}: expected a time of day, got {value!r}")
            elif value.tzinfo is not None:
                errors.append(f"{key}: must not carry a time zone (local time)")
        if self.failsafe_window_start == self.failsafe_window_end:
            errors.append("failsafe window: start and end must differ")
        weekday: object = self.valve_exercise_weekday
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            errors.append(
                f"valve_exercise_weekday: expected 0 (Monday) to 6 (Sunday), got {weekday!r}"
            )
        if errors:
            raise ConfigError(errors)


def _suggest_zone_id(value: str) -> str | None:
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_only.lower()).strip("_")
    if not slug:
        return None
    return slug if slug[0].isalpha() else f"zone_{slug}"


def _zone_id_error(value: object) -> str | None:
    if not isinstance(value, str):
        return f"zone id must be a string, got {value!r}"
    if _ZONE_ID.fullmatch(value):
        return None
    error = (
        f"zone id {value!r} is invalid: use lowercase letters, digits and '_', "
        "starting with a letter"
    )
    suggestion = _suggest_zone_id(value)
    return f"{error} (e.g. {suggestion!r})" if suggestion else error


@dataclass(frozen=True)
class ZoneConfig:
    """One zone as wired in YAML (§5.6).

    `id` is the stable key for persisted state, schedules and entity unique IDs (D-76):
    an HA-style slug (D-84). `name` is for display only.
    """

    id: str
    name: str
    has_valve: bool = True
    sensor_offset: float = 0.0

    def __post_init__(self) -> None:
        errors: list[str] = []
        if id_error := _zone_id_error(self.id):
            errors.append(id_error)
        name: object = self.name
        if not isinstance(name, str):
            errors.append(f"name must be a string, got {name!r}")
        elif not name.strip():
            errors.append("name must not be empty")
        has_valve: object = self.has_valve
        if not isinstance(has_valve, bool):
            errors.append(f"has_valve must be true or false, got {has_valve!r}")
        if offset_error := SENSOR_OFFSET_SPEC.check(self.sensor_offset):
            errors.append(offset_error)
        if errors:
            prefix = f"zone {self.id!r}: "
            raise ConfigError([e if e.startswith("zone id") else prefix + e for e in errors])


@dataclass(frozen=True)
class CoreConfig:
    """Static configuration passed to `step` (§5.3).

    `zones` keeps the YAML order, which breaks calling-zone ties (D-65).
    """

    zones: tuple[ZoneConfig, ...]
    plausible_min: float = 0.0  # plausibility range of readings, °C (D-77)
    plausible_max: float = 40.0
    output_mismatch_alert: int = 3  # consecutive reconcile intervals (D-67)

    def __post_init__(self) -> None:
        errors = self._zone_errors()
        low: object = self.plausible_min
        high: object = self.plausible_max
        if not (_is_finite_number(low) and _is_finite_number(high) and low < high):
            errors.append(
                f"plausible range: expected finite numbers with min < max, got {low!r} to {high!r}"
            )
        alert: object = self.output_mismatch_alert
        if isinstance(alert, bool) or not isinstance(alert, int) or alert < 1:
            errors.append(f"output_mismatch_alert: expected an integer ≥ 1, got {alert!r}")
        if errors:
            raise ConfigError(errors)

    @property
    def zone_ids(self) -> tuple[str, ...]:
        return tuple(zone.id for zone in self.zones)

    def _zone_errors(self) -> list[str]:
        if not self.zones:
            return ["at least one zone must be configured"]
        errors: list[str] = []
        ids: set[str] = set()
        names: dict[str, str] = {}
        for zone in self.zones:
            if not isinstance(zone, ZoneConfig):
                errors.append(f"zones: expected ZoneConfig, got {zone!r}")
                continue
            if zone.id in ids:
                errors.append(f"duplicate zone id {zone.id!r}")
            ids.add(zone.id)
            folded = zone.name.strip().casefold()  # D-85
            if folded in names:
                errors.append(
                    f"duplicate zone name {zone.name!r} (zones {names[folded]!r} and {zone.id!r})"
                )
            else:
                names[folded] = zone.id
        return errors


def _is_finite_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def config_warnings(config: CoreConfig) -> list[str]:
    """Non-fatal problems for the adapter to log at startup."""
    warnings: list[str] = []
    if all(zone.has_valve for zone in config.zones):
        warnings.append(  # D-80
            "Every zone has a valve. The integration assumes a flow path whenever the heat "
            "source request is ON (an unvalved zone, a bypass or a buffer/hydraulic separator). "
            "Make sure your installation has one."
        )
    return warnings
