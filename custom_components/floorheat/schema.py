"""YAML configuration of floorheat (docs/design.md §5.6, docs/configuration.md).

Two stages:
- `CONFIG_SCHEMA` checks the structure and everything that does not depend on HA's
  unit system (zone ids and names, duplicate switches), so `ha core check` finds it;
- `build_config` converts the temperatures from HA's unit system to °C (D-77, D-111)
  and builds the core configuration, reporting every remaining problem at once.

Entity existence is checked after HA has started (D-107), not here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_HEAT_SOURCE_SWITCH,
    CONF_ID,
    CONF_NAME,
    CONF_OUTPUT_MISMATCH_ALERT,
    CONF_PLAUSIBLE_MAX,
    CONF_PLAUSIBLE_MIN,
    CONF_POWER_SENSOR,
    CONF_RECONCILE_INTERVAL,
    CONF_SENSOR,
    CONF_SENSOR_OFFSET,
    CONF_VALVE,
    CONF_ZONES,
    DEFAULT_RECONCILE_INTERVAL,
    DOMAIN,
    MAX_RECONCILE_INTERVAL,
    MIN_RECONCILE_INTERVAL,
    NO_VALVE,
)
from .core.config import ConfigError, CoreConfig, ZoneConfig
from .core.units import TemperatureUnit, delta_to_celsius, to_celsius


def _valve(value: Any) -> str | None:
    """A switch entity, or `none` for a zone without a valve."""
    if isinstance(value, str) and value.strip().lower() == NO_VALVE:
        return None
    try:
        return str(cv.entity_domain("switch")(value))
    except vol.Invalid as err:
        raise vol.Invalid(f"expected a switch entity or '{NO_VALVE}' ({err})") from None


ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SENSOR): cv.entity_domain("sensor"),
        vol.Required(CONF_VALVE): _valve,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_domain("sensor"),
        vol.Optional(CONF_SENSOR_OFFSET, default=0.0): vol.Coerce(float),
    }
)


def _check_wiring(conf: dict[str, Any]) -> dict[str, Any]:
    """Unit-independent checks: zone ids and names (D-84, D-85), duplicate switches."""
    errors: list[str] = []
    zones: list[ZoneConfig] = []
    for zone in conf[CONF_ZONES]:
        try:
            zones.append(ZoneConfig(id=zone[CONF_ID], name=zone[CONF_NAME]))
        except ConfigError as err:
            errors.extend(err.errors)
    if not errors:
        try:
            CoreConfig(zones=tuple(zones))
        except ConfigError as err:
            errors.extend(err.errors)
    switches = [conf[CONF_HEAT_SOURCE_SWITCH]] + [
        zone[CONF_VALVE] for zone in conf[CONF_ZONES] if zone[CONF_VALVE] is not None
    ]
    errors.extend(
        f"switch {entity_id} is mapped more than once"
        for entity_id, count in Counter(switches).items()
        if count > 1
    )
    if errors:
        raise vol.Invalid("; ".join(errors))
    return conf


FLOORHEAT_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_HEAT_SOURCE_SWITCH): cv.entity_domain("switch"),
            vol.Required(CONF_ZONES): vol.All(cv.ensure_list, vol.Length(min=1), [ZONE_SCHEMA]),
            vol.Optional(CONF_PLAUSIBLE_MIN): vol.Coerce(float),
            vol.Optional(CONF_PLAUSIBLE_MAX): vol.Coerce(float),
            vol.Optional(CONF_RECONCILE_INTERVAL, default=DEFAULT_RECONCILE_INTERVAL): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_RECONCILE_INTERVAL, max=MAX_RECONCILE_INTERVAL)
            ),
            vol.Optional(CONF_OUTPUT_MISMATCH_ALERT, default=3): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
        }
    ),
    _check_wiring,
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: FLOORHEAT_SCHEMA}, extra=vol.ALLOW_EXTRA)


@dataclass(frozen=True)
class ZoneWiring:
    """The HA entities of one zone."""

    id: str
    sensor: str
    valve: str | None
    power_sensor: str | None


@dataclass(frozen=True)
class FloorheatConfig:
    """The validated YAML configuration."""

    core: CoreConfig
    heat_source: str
    zones: tuple[ZoneWiring, ...]
    reconcile_interval: timedelta

    @property
    def valves(self) -> dict[str, str]:
        """Valve switch per valved zone id, in YAML order."""
        return {zone.id: zone.valve for zone in self.zones if zone.valve is not None}

    @property
    def switches(self) -> tuple[str, ...]:
        """Every output switch: the heat source first, then the valves."""
        return (self.heat_source, *self.valves.values())

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Every mapped entity (sensors, switches, power sensors)."""
        ids = [*self.switches]
        for zone in self.zones:
            ids.append(zone.sensor)
            if zone.power_sensor is not None:
                ids.append(zone.power_sensor)
        return tuple(dict.fromkeys(ids))


def build_config(conf: dict[str, Any], unit: TemperatureUnit) -> FloorheatConfig:
    """Build the configuration from `FLOORHEAT_SCHEMA` output.

    YAML temperatures are in HA's unit system and converted to °C (D-111). Raises
    `ConfigError` listing every problem.
    """
    errors: list[str] = []
    zones: list[ZoneConfig] = []
    for zone in conf[CONF_ZONES]:
        try:
            zones.append(
                ZoneConfig(
                    id=zone[CONF_ID],
                    name=zone[CONF_NAME],
                    has_valve=zone[CONF_VALVE] is not None,
                    sensor_offset=delta_to_celsius(zone[CONF_SENSOR_OFFSET], unit),
                )
            )
        except ConfigError as err:
            errors.extend(err.errors)
    if errors:
        raise ConfigError(errors)
    defaults = CoreConfig(zones=tuple(zones))
    core = CoreConfig(
        zones=tuple(zones),
        plausible_min=(
            to_celsius(conf[CONF_PLAUSIBLE_MIN], unit)
            if CONF_PLAUSIBLE_MIN in conf
            else defaults.plausible_min
        ),
        plausible_max=(
            to_celsius(conf[CONF_PLAUSIBLE_MAX], unit)
            if CONF_PLAUSIBLE_MAX in conf
            else defaults.plausible_max
        ),
        output_mismatch_alert=conf[CONF_OUTPUT_MISMATCH_ALERT],
    )
    return FloorheatConfig(
        core=core,
        heat_source=conf[CONF_HEAT_SOURCE_SWITCH],
        zones=tuple(
            ZoneWiring(
                id=zone[CONF_ID],
                sensor=zone[CONF_SENSOR],
                valve=zone[CONF_VALVE],
                power_sensor=zone.get(CONF_POWER_SENSOR),
            )
            for zone in conf[CONF_ZONES]
        ),
        reconcile_interval=timedelta(seconds=conf[CONF_RECONCILE_INTERVAL]),
    )
