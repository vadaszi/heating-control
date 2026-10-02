"""Persistence of the logic state and the UI settings.

One `helpers.storage.Store` file holds:
- `core`: `CoreState.to_dict()`, restored with `load_state` (versioned by the core);
- `settings`: the values changed from the UI (zone and global parameters, heating
  season, control active, schedules, holiday); the adapter owns them and the entities
  and services only show and change them;
- `pending_off`: switches that still have to confirm the final OFF after Control active
  was switched OFF;
- `heartbeat`: per Shelly (`ShellyWiring.key`), failed heartbeat calls in a row and the
  alerts sent, so a restart neither repeats an alert nor loses a recovery.

Saves are delayed and coalesced (at most one write per `SAVE_DELAY`); a pending save is
written when HA stops.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import SAVE_DELAY, STORAGE_KEY, STORAGE_VERSION
from .core.config import (
    GLOBAL_PARAM_SPECS,
    TIME_OF_DAY_PARAMS,
    ZONE_PARAM_SPECS,
    ConfigError,
    CoreConfig,
    GlobalParams,
    ParamSpec,
    ZoneParams,
)
from .core.heartbeat import HeartbeatTracking
from .core.schedule import Schedule, load_schedules, schedules_to_list
from .core.state import CoreState, load_state

_LOGGER = logging.getLogger(__name__)

HOLIDAY_END_TIME = time(12, 0)  # the holiday end time on a first install


@dataclass(frozen=True)
class Settings:
    """Values changed from the UI; defaults on a first install."""

    zone_params: Mapping[str, ZoneParams]
    global_params: GlobalParams = field(default_factory=GlobalParams)
    heating_season: bool = True
    control_active: bool = False  # shadow mode on first install
    schedules: tuple[Schedule, ...] = ()
    schedule_counter: int = 0  # the last schedule number handed out; never reused
    holiday_on: bool = False
    holiday_end_date: date | None = None  # None: no end
    holiday_end_time: time = HOLIDAY_END_TIME  # local; never empty, kept when holiday ends

    @classmethod
    def defaults(cls, config: CoreConfig) -> Settings:
        return cls(zone_params={zone_id: ZoneParams() for zone_id in config.zone_ids})

    def to_dict(self) -> dict[str, Any]:
        return {
            "zones": {
                zone_id: _params_to_dict(params, ZONE_PARAM_SPECS)
                for zone_id, params in self.zone_params.items()
            },
            "global": {
                **_params_to_dict(self.global_params, GLOBAL_PARAM_SPECS),
                **{key: getattr(self.global_params, key).isoformat() for key in TIME_OF_DAY_PARAMS},
                "valve_exercise_weekday": self.global_params.valve_exercise_weekday,
            },
            "heating_season": self.heating_season,
            "control_active": self.control_active,
            "schedules": schedules_to_list(self.schedules),
            "schedule_counter": self.schedule_counter,
            "holiday_on": self.holiday_on,
            "holiday_end_date": None
            if self.holiday_end_date is None
            else self.holiday_end_date.isoformat(),
            "holiday_end_time": self.holiday_end_time.isoformat(),
        }

    @classmethod
    def from_dict(
        cls, data: object, config: CoreConfig, time_zone: tzinfo = UTC
    ) -> tuple[Settings, list[str]]:
        """Parse `to_dict` output; unusable parts fall back to their defaults.

        `time_zone` (HA's) turns a 0.8.0 holiday end into a local date and time.
        """
        defaults = cls.defaults(config)
        if data is None:
            return defaults, []
        if not isinstance(data, Mapping):
            return defaults, [f"Stored settings are unusable ({data!r}); using defaults."]
        warnings: list[str] = []
        zones = data.get("zones")
        zones = zones if isinstance(zones, Mapping) else {}
        zone_params = {
            zone_id: _params_from(ZoneParams, zones.get(zone_id), ZONE_PARAM_SPECS, warnings)
            for zone_id in config.zone_ids
        }
        global_data = data.get("global")
        zone_params = _migrate_holiday_temp(zone_params, global_data)
        global_params = _params_from(
            GlobalParams, global_data, GLOBAL_PARAM_SPECS, warnings, _other_globals(global_data)
        )
        season = data.get("heating_season", defaults.heating_season)
        control = data.get("control_active", defaults.control_active)
        if not isinstance(season, bool) or not isinstance(control, bool):
            warnings.append("Stored heating season / control active are unusable; using defaults.")
            season, control = defaults.heating_season, defaults.control_active
        schedules, schedule_warnings = load_schedules(data.get("schedules"), config)
        warnings += schedule_warnings
        holiday_on, end_date, end_time = _holiday(data, warnings, time_zone)
        return cls(
            zone_params,
            global_params,
            season,
            control,
            schedules,
            _counter(data.get("schedule_counter"), schedules),
            holiday_on,
            end_date,
            end_time,
        ), warnings


def _counter(value: object, schedules: tuple[Schedule, ...]) -> int:
    """The stored schedule counter, never below a number already in use."""
    counter = value if isinstance(value, int) and not isinstance(value, bool) else 0
    used = [int(s.id) for s in schedules if s.id.isdigit()]
    return max([counter, *used])


def _holiday(
    data: Mapping[str, Any], warnings: list[str], time_zone: tzinfo
) -> tuple[bool, date | None, time]:
    """Holiday on, its end date and its end time."""
    on = data.get("holiday_on", False)
    try:
        end_date, end_time = _holiday_end(data, time_zone)
    except TypeError, ValueError:
        warnings.append("Stored holiday is unusable; holiday is off.")
        return False, None, HOLIDAY_END_TIME
    if not isinstance(on, bool):
        warnings.append("Stored holiday is unusable; holiday is off.")
        return False, None, end_time
    return on, end_date, end_time


def _holiday_end(data: Mapping[str, Any], time_zone: tzinfo) -> tuple[date | None, time]:
    """The stored end; a 0.8.0 `holiday_end` (an aware datetime) becomes local date and
    time. Raises `TypeError` / `ValueError` if it is unusable."""
    if "holiday_end" in data and "holiday_end_date" not in data:
        old = data["holiday_end"]
        if old is None:
            return None, HOLIDAY_END_TIME
        end = datetime.fromisoformat(old)
        if end.tzinfo is None:
            raise ValueError("the stored holiday end has no time zone")
        local = end.astimezone(time_zone)
        return local.date(), local.time().replace(second=0, microsecond=0)
    stored_date, stored_time = data.get("holiday_end_date"), data.get("holiday_end_time")
    end_date = None if stored_date is None else date.fromisoformat(stored_date)
    end_time = HOLIDAY_END_TIME if stored_time is None else time.fromisoformat(stored_time)
    return end_date, end_time


def _params_to_dict(params: object, specs: Mapping[str, ParamSpec]) -> dict[str, Any]:
    return {key: spec.to_number(getattr(params, key)) for key, spec in specs.items()}


def _params_from[P: (ZoneParams, GlobalParams)](
    cls: Callable[..., P],
    data: object,
    specs: Mapping[str, ParamSpec],
    warnings: list[str],
    extra: Mapping[str, Any] | None = None,
) -> P:
    if not isinstance(data, Mapping):
        return cls()
    values: dict[str, Any] = dict(extra or {})
    for key, spec in specs.items():
        number = data.get(key)
        if isinstance(number, int | float) and not isinstance(number, bool):
            values[key] = spec.from_number(number)
    try:
        return cls(**values)
    except ConfigError as err:
        warnings.append(f"Stored parameters are unusable ({err.errors}); using defaults.")
        return cls()


def _migrate_holiday_temp(
    zone_params: dict[str, ZoneParams], global_data: object
) -> dict[str, ZoneParams]:
    """Up to 0.7 HolidayTemp was global; it becomes every zone's value. The next
    save no longer holds the global key."""
    value = global_data.get("holiday_temp") if isinstance(global_data, Mapping) else None
    spec = ZONE_PARAM_SPECS["holiday_temp"]
    if value is None or spec.check(value) is not None:
        return zone_params
    assert isinstance(value, int | float)  # checked by the spec
    _LOGGER.info("The global holiday temperature %s °C is now every zone's own value", value)
    return {
        zone_id: dataclasses.replace(params, holiday_temp=float(value))
        for zone_id, params in zone_params.items()
    }


def _other_globals(data: object) -> dict[str, time | int]:
    """The stored global parameters that are no number spec: the times of day and the
    valve exercise weekday, as far as readable (missing ones keep their defaults)."""
    if not isinstance(data, Mapping):
        return {}
    values: dict[str, time | int] = {}
    for key in TIME_OF_DAY_PARAMS:
        value = data.get(key)
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                values[key] = time.fromisoformat(value)
    weekday = data.get("valve_exercise_weekday")
    if isinstance(weekday, int) and not isinstance(weekday, bool):
        values["valve_exercise_weekday"] = weekday
    return values


@dataclass(frozen=True)
class StoredData:
    """What was restored at startup."""

    core: CoreState
    settings: Settings
    pending_off: frozenset[str]
    warnings: list[str]
    heartbeat: Mapping[str, HeartbeatTracking] = field(default_factory=dict)


class FloorHeatingStore:
    """The integration's storage file."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._latest: dict[str, Any] | None = None
        self._save_pending = False

    async def async_load(
        self,
        config: CoreConfig,
        switches: tuple[str, ...],
        shellys: tuple[str, ...] = (),
        time_zone: tzinfo = UTC,
    ) -> StoredData:
        """Restore the stored data for `config`; unusable parts start as on a first start.

        `shellys` are the keys of the configured Shellys; others are dropped.
        """
        try:
            data: object = await self._store.async_load()
        except Exception as err:  # corrupt file: start fresh
            data = {}
            warnings = [f"Stored data is unreadable ({err}); starting fresh."]
        else:
            warnings = []
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            warnings.append(f"Stored data is unusable ({data!r}); starting fresh.")
            data = {}
        core, core_warnings = load_state(data.get("core"), config)
        settings, settings_warnings = Settings.from_dict(data.get("settings"), config, time_zone)
        pending = data.get("pending_off")
        pending_off = frozenset(
            entity_id
            for entity_id in (pending if isinstance(pending, list) else [])
            if entity_id in switches
        )
        stored_heartbeat = data.get("heartbeat")
        if not isinstance(stored_heartbeat, Mapping):
            stored_heartbeat = {}
        heartbeat = {key: HeartbeatTracking.from_dict(stored_heartbeat.get(key)) for key in shellys}
        return StoredData(
            core,
            settings,
            pending_off,
            warnings + core_warnings + settings_warnings,
            heartbeat,
        )

    def schedule_save(self, data: dict[str, Any]) -> None:
        """Write `data` after `SAVE_DELAY`; later calls before the write replace it."""
        self._latest = data
        if not self._save_pending:
            self._save_pending = True
            self._store.async_delay_save(self._take, SAVE_DELAY)

    def _take(self) -> dict[str, Any]:
        self._save_pending = False
        assert self._latest is not None
        return self._latest

    async def async_save_now(self, data: dict[str, Any]) -> None:
        """Write at once (on stop); replaces a pending delayed save."""
        self._latest = data
        self._save_pending = False
        await self._store.async_save(data)
