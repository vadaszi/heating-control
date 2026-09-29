"""Persistence of the logic state and the UI settings (docs/design.md §3.8, D-87, D-106).

One `helpers.storage.Store` file holds:
- `core`: `CoreState.to_dict()`, restored with `load_state` (versioned by the core);
- `settings`: the values changed from the UI (zone and global parameters, heating
  season, control active); the adapter owns them and the entities only show and change
  them (D-106);
- `pending_off`: switches that still have to confirm the final OFF after Control active
  was switched OFF (D-110);
- `heartbeat`: per Shelly (`ShellyWiring.key`), failed heartbeat calls in a row and the
  alerts sent (D-121), so a restart neither repeats an alert nor loses a recovery.

Saves are delayed and coalesced (at most one write per `SAVE_DELAY`); a pending save is
written when HA stops.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import SAVE_DELAY, STORAGE_KEY, STORAGE_VERSION
from .core.config import (
    GLOBAL_PARAM_SPECS,
    ZONE_PARAM_SPECS,
    ConfigError,
    CoreConfig,
    GlobalParams,
    ParamSpec,
    ZoneParams,
)
from .core.heartbeat import HeartbeatTracking
from .core.state import CoreState, load_state

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """Values changed from the UI (§4); defaults on a first install (§4, §5.5)."""

    zone_params: Mapping[str, ZoneParams]
    global_params: GlobalParams = field(default_factory=GlobalParams)
    heating_season: bool = True
    control_active: bool = False  # shadow mode on first install (§5.5)

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
                "sensor_fault_reminder": self.global_params.sensor_fault_reminder.isoformat(),
            },
            "heating_season": self.heating_season,
            "control_active": self.control_active,
        }

    @classmethod
    def from_dict(cls, data: object, config: CoreConfig) -> tuple[Settings, list[str]]:
        """Parse `to_dict` output; unusable parts fall back to their defaults."""
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
        global_params = _params_from(
            GlobalParams, global_data, GLOBAL_PARAM_SPECS, warnings, _reminder(global_data)
        )
        season = data.get("heating_season", defaults.heating_season)
        control = data.get("control_active", defaults.control_active)
        if not isinstance(season, bool) or not isinstance(control, bool):
            warnings.append("Stored heating season / control active are unusable; using defaults.")
            season, control = defaults.heating_season, defaults.control_active
        return cls(zone_params, global_params, season, control), warnings


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


def _reminder(data: object) -> dict[str, time]:
    """The stored SensorFaultReminder, if readable."""
    value = data.get("sensor_fault_reminder") if isinstance(data, Mapping) else None
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return {"sensor_fault_reminder": time.fromisoformat(value)}
    return {}


@dataclass(frozen=True)
class StoredData:
    """What was restored at startup."""

    core: CoreState
    settings: Settings
    pending_off: frozenset[str]
    warnings: list[str]
    heartbeat: Mapping[str, HeartbeatTracking] = field(default_factory=dict)


class FloorheatStore:
    """The integration's storage file."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._latest: dict[str, Any] | None = None
        self._save_pending = False

    async def async_load(
        self, config: CoreConfig, switches: tuple[str, ...], shellys: tuple[str, ...] = ()
    ) -> StoredData:
        """Restore the stored data for `config`; unusable parts start as on a first start.

        `shellys` are the keys of the configured Shellys; others are dropped.
        """
        try:
            data: object = await self._store.async_load()
        except Exception as err:  # corrupt file: start fresh (D-87)
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
        settings, settings_warnings = Settings.from_dict(data.get("settings"), config)
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
