"""YAML configuration of the integration.

Two stages:
- `CONFIG_SCHEMA` checks the structure and everything that does not depend on HA's
  unit system (zone ids and names, duplicate switches), so `ha core check` finds it;
- `build_config` converts the temperatures from HA's unit system to °C
  and builds the core configuration, reporting every remaining problem at once.

Entity existence is checked after HA has started, not here.

Shelly watchdogs: every mapped switch is either on a Shelly listed under
`shellys` (address, script id, the switches on it) or listed in `no_watchdog`. The Shelly
holding the heat source switch runs the heat source script and holds nothing else; every
other Shelly runs the valve script.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_HEARTBEAT_CHECK_INTERVAL,
    CONF_HEARTBEAT_FAIL_ALERT,
    CONF_HEARTBEAT_INTERVAL,
    CONF_HEARTBEAT_TIMEOUT,
    CONF_HEAT_SOURCE_SWITCH,
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_NO_WATCHDOG,
    CONF_NOTIFY,
    CONF_OUTPUT_MISMATCH_ALERT,
    CONF_PASSWORD,
    CONF_PLAUSIBLE_MAX,
    CONF_PLAUSIBLE_MIN,
    CONF_RECONCILE_INTERVAL,
    CONF_SCRIPT_ID,
    CONF_SENSOR,
    CONF_SENSOR_OFFSET,
    CONF_SHELLYS,
    CONF_SWITCHES,
    CONF_VALVE,
    CONF_WATCHDOG_PING_INTERVAL,
    CONF_WATCHDOG_PING_URL,
    CONF_ZONES,
    DEFAULT_HEARTBEAT_FAIL_ALERT,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_HEARTBEAT_TIMEOUT,
    DEFAULT_RECONCILE_INTERVAL,
    DEFAULT_WATCHDOG_PING_INTERVAL,
    DOMAIN,
    MAX_HEARTBEAT_INTERVAL,
    MAX_RECONCILE_INTERVAL,
    MAX_SCRIPT_SECONDS,
    MAX_WATCHDOG_PING_INTERVAL,
    MIN_HEARTBEAT_INTERVAL,
    MIN_RECONCILE_INTERVAL,
    MIN_WATCHDOG_PING_INTERVAL,
    NO_VALVE,
)
from .core.config import ConfigError, CoreConfig, ZoneConfig
from .core.heartbeat import ExpectedParams, ShellyRole
from .core.units import TemperatureUnit, delta_to_celsius, to_celsius

NOTIFY_TARGET = re.compile(r"notify\.[a-z0-9_]+")
HOST = re.compile(r"[^\s/?#@]+")  # a host name or address, optionally with :port


def _valve(value: Any) -> str | None:
    """A switch entity, or `none` for a zone without a valve."""
    if isinstance(value, str) and value.strip().lower() == NO_VALVE:
        return None
    try:
        return str(cv.entity_domain("switch")(value))
    except vol.Invalid as err:
        raise vol.Invalid(f"expected a switch entity or '{NO_VALVE}' ({err})") from None


def _notify_target(value: Any) -> str:
    """A notify service or notify entity, written `notify.<name>`."""
    target = cv.string(value).strip()
    if not NOTIFY_TARGET.fullmatch(target):
        raise vol.Invalid(f"expected a notify target like 'notify.mobile_app_phone', got {value!r}")
    return target


def _ping_url(value: Any) -> str:
    """An http(s) URL; the error never repeats it, because it is a secret."""
    try:
        return str(cv.url(value))
    except vol.Invalid:
        raise vol.Invalid("expected an http:// or https:// URL") from None


def _host(value: Any) -> str:
    """The Shelly's address as in the device URL, without `http://` or a path."""
    host = cv.string(value).strip()
    if not HOST.fullmatch(host):
        raise vol.Invalid(f"expected a host name or address like '192.0.2.10', got {value!r}")
    return host


SHELLY_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME): cv.string,
        vol.Required(CONF_HOST): _host,
        vol.Required(CONF_SCRIPT_ID): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Required(CONF_SWITCHES): vol.All(
            cv.ensure_list, vol.Length(min=1), [cv.entity_domain("switch")]
        ),
        vol.Optional(CONF_PASSWORD): cv.string,
    }
)

_SECONDS = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_SCRIPT_SECONDS))

ZONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SENSOR): cv.entity_domain("sensor"),
        vol.Required(CONF_VALVE): _valve,
        vol.Optional(CONF_SENSOR_OFFSET, default=0.0): vol.Coerce(float),
    }
)


def _check_wiring(conf: dict[str, Any]) -> dict[str, Any]:
    """Unit-independent checks: zone ids and names, duplicate switches."""
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
    errors.extend(_check_watchdogs(conf, switches))
    if errors:
        raise vol.Invalid("; ".join(errors))
    return conf


def _shelly_name(shelly: dict[str, Any]) -> str:
    return str(shelly.get(CONF_NAME, shelly[CONF_HOST]))


def _check_watchdogs(conf: dict[str, Any], switches: list[str]) -> list[str]:
    """Every mapped switch is on a listed Shelly or in `no_watchdog`."""
    errors: list[str] = []
    mapped = set(switches)
    heat_source = conf[CONF_HEAT_SOURCE_SWITCH]
    no_watchdog = set(conf[CONF_NO_WATCHDOG])
    errors.extend(
        f"no_watchdog: {entity_id} is not a mapped switch (heat_source_switch or a valve)"
        for entity_id in dict.fromkeys(conf[CONF_NO_WATCHDOG])
        if entity_id not in mapped
    )
    on_shelly: set[str] = set()
    for shelly in conf[CONF_SHELLYS]:
        name = _shelly_name(shelly)
        for entity_id in shelly[CONF_SWITCHES]:
            if entity_id not in mapped:
                errors.append(f"shelly {name}: {entity_id} is not a mapped switch")
            elif entity_id in on_shelly:
                errors.append(f"switch {entity_id} is listed on more than one Shelly")
            elif entity_id in no_watchdog:
                errors.append(f"switch {entity_id} is on Shelly {name} and in no_watchdog")
            on_shelly.add(entity_id)
        if heat_source in shelly[CONF_SWITCHES] and len(set(shelly[CONF_SWITCHES])) > 1:
            errors.append(
                f"shelly {name}: the heat source switch needs a Shelly of its own "
                "(no valves on the same device)"
            )
    errors.extend(
        f"switch {entity_id} has no watchdog: add it to the switches of its Shelly under "
        "shellys, or to no_watchdog if it is not a Shelly running the watchdog script"
        for entity_id in dict.fromkeys(switches)
        if entity_id not in on_shelly and entity_id not in no_watchdog
    )
    addresses = Counter(
        (shelly[CONF_HOST].lower(), shelly[CONF_SCRIPT_ID]) for shelly in conf[CONF_SHELLYS]
    )
    errors.extend(
        f"shellys: {host} script {script_id} is listed more than once"
        for (host, script_id), count in addresses.items()
        if count > 1
    )
    names = Counter(_shelly_name(shelly).strip().casefold() for shelly in conf[CONF_SHELLYS])
    errors.extend(
        f"shellys: the name {name!r} is used more than once"
        for name, count in names.items()
        if count > 1
    )
    if conf[CONF_HEARTBEAT_INTERVAL] >= conf[CONF_HEARTBEAT_TIMEOUT]:
        errors.append("heartbeat_interval must be shorter than heartbeat_timeout")
    return errors


FLOOR_HEATING_SCHEMA = vol.All(
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
            vol.Optional(CONF_NOTIFY, default=list): vol.All(cv.ensure_list, [_notify_target]),
            vol.Optional(CONF_SHELLYS, default=list): vol.All(cv.ensure_list, [SHELLY_SCHEMA]),
            vol.Optional(CONF_NO_WATCHDOG, default=list): vol.All(
                cv.ensure_list, [cv.entity_domain("switch")]
            ),
            vol.Optional(CONF_HEARTBEAT_INTERVAL, default=DEFAULT_HEARTBEAT_INTERVAL): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_HEARTBEAT_INTERVAL, max=MAX_HEARTBEAT_INTERVAL)
            ),
            vol.Optional(CONF_HEARTBEAT_FAIL_ALERT, default=DEFAULT_HEARTBEAT_FAIL_ALERT): vol.All(
                vol.Coerce(int), vol.Range(min=1)
            ),
            vol.Optional(CONF_HEARTBEAT_TIMEOUT, default=DEFAULT_HEARTBEAT_TIMEOUT): _SECONDS,
            vol.Optional(CONF_HEARTBEAT_CHECK_INTERVAL): _SECONDS,
            vol.Optional(CONF_WATCHDOG_PING_URL): _ping_url,
            vol.Optional(
                CONF_WATCHDOG_PING_INTERVAL, default=DEFAULT_WATCHDOG_PING_INTERVAL
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_WATCHDOG_PING_INTERVAL, max=MAX_WATCHDOG_PING_INTERVAL),
            ),
        }
    ),
    _check_wiring,
)

CONFIG_SCHEMA = vol.Schema({DOMAIN: FLOOR_HEATING_SCHEMA}, extra=vol.ALLOW_EXTRA)


@dataclass(frozen=True)
class ZoneWiring:
    """The HA entities of one zone."""

    id: str
    sensor: str
    valve: str | None


@dataclass(frozen=True)
class ShellyWiring:
    """A Shelly running a watchdog script."""

    name: str
    host: str
    script_id: int
    switches: tuple[str, ...]
    role: ShellyRole
    password: str | None = None

    @property
    def key(self) -> str:
        """Identifies the Shelly in the stored heartbeat state."""
        return f"{self.host}/{self.script_id}"

    @property
    def url(self) -> str:
        return f"http://{self.host}/script/{self.script_id}/heartbeat"


@dataclass(frozen=True)
class FloorHeatingConfig:
    """The validated YAML configuration."""

    core: CoreConfig
    heat_source: str
    zones: tuple[ZoneWiring, ...]
    reconcile_interval: timedelta
    notify: tuple[str, ...] = ()  # notify services or notify entities
    shellys: tuple[ShellyWiring, ...] = ()
    heartbeat_interval: timedelta = timedelta(seconds=DEFAULT_HEARTBEAT_INTERVAL)
    heartbeat_fail_alert: int = DEFAULT_HEARTBEAT_FAIL_ALERT
    expected_params: ExpectedParams = field(default_factory=ExpectedParams)
    watchdog_ping_url: str | None = field(default=None, repr=False)  # a secret
    watchdog_ping_interval: timedelta = timedelta(seconds=DEFAULT_WATCHDOG_PING_INTERVAL)

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
        """Every mapped entity (sensors and switches)."""
        return tuple(dict.fromkeys([*self.switches, *(zone.sensor for zone in self.zones)]))


def build_config(conf: dict[str, Any], unit: TemperatureUnit) -> FloorHeatingConfig:
    """Build the configuration from `FLOOR_HEATING_SCHEMA` output.

    YAML temperatures are in HA's unit system and converted to °C. Raises
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
    return FloorHeatingConfig(
        core=core,
        heat_source=conf[CONF_HEAT_SOURCE_SWITCH],
        zones=tuple(
            ZoneWiring(
                id=zone[CONF_ID],
                sensor=zone[CONF_SENSOR],
                valve=zone[CONF_VALVE],
            )
            for zone in conf[CONF_ZONES]
        ),
        reconcile_interval=timedelta(seconds=conf[CONF_RECONCILE_INTERVAL]),
        notify=tuple(dict.fromkeys(conf[CONF_NOTIFY])),
        shellys=tuple(
            ShellyWiring(
                name=_shelly_name(shelly),
                host=shelly[CONF_HOST],
                script_id=shelly[CONF_SCRIPT_ID],
                switches=tuple(dict.fromkeys(shelly[CONF_SWITCHES])),
                role=(
                    ShellyRole.HEAT_SOURCE
                    if conf[CONF_HEAT_SOURCE_SWITCH] in shelly[CONF_SWITCHES]
                    else ShellyRole.VALVE
                ),
                password=shelly.get(CONF_PASSWORD),
            )
            for shelly in conf[CONF_SHELLYS]
        ),
        heartbeat_interval=timedelta(seconds=conf[CONF_HEARTBEAT_INTERVAL]),
        heartbeat_fail_alert=conf[CONF_HEARTBEAT_FAIL_ALERT],
        expected_params=ExpectedParams(
            heartbeat_timeout_s=conf[CONF_HEARTBEAT_TIMEOUT],
            check_interval_s=conf.get(CONF_HEARTBEAT_CHECK_INTERVAL),
        ),
        watchdog_ping_url=conf.get(CONF_WATCHDOG_PING_URL),
        watchdog_ping_interval=timedelta(seconds=conf[CONF_WATCHDOG_PING_INTERVAL]),
    )
