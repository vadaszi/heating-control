"""Multizone Floor Heating Manager integration.

Configured in YAML. The validated YAML is imported into a single config entry, which owns the
devices and entities. The control logic lives in `core/` (no HA imports); this package is the thin
HA adapter around it.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DATA_YAML, DOMAIN, GLOBAL_DEVICE
from .controller import FloorHeatingController
from .core.config import ConfigError, config_warnings
from .core.units import TemperatureUnit
from .heartbeat import HeartbeatClient
from .notifications import Notifier
from .runtime import FloorHeatingConfigEntry, FloorHeatingRuntime
from .schema import CONFIG_SCHEMA, FloorHeatingConfig, build_config
from .services import async_register as async_register_services
from .storage import FloorHeatingStore
from .watchdog import WatchdogPing

__all__ = ["CONFIG_SCHEMA", "DOMAIN", "async_setup", "async_setup_entry", "async_unload_entry"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.DATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services; validate the YAML and import it into the config entry."""
    async_register_services(hass)
    if DOMAIN not in config:
        return True
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    try:
        yaml_config = build_config(config[DOMAIN], unit)
    except ConfigError as err:
        _LOGGER.error("%s", err)
        return False
    for warning in config_warnings(yaml_config.core):
        _LOGGER.warning("%s", warning)
    hass.data[DATA_YAML] = yaml_config
    if not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data={}),
            f"{DOMAIN} import",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FloorHeatingConfigEntry) -> bool:
    """Start the control for the YAML configuration."""
    config = hass.data.get(DATA_YAML)
    if config is None:  # the YAML section was removed; keep the entry and its data
        raise ConfigEntryError(
            f"No `{DOMAIN}:` section in configuration.yaml. Add it again and restart, "
            "or delete this entry."
        )
    store = FloorHeatingStore(hass)
    stored = await store.async_load(
        config.core,
        config.switches,
        tuple(shelly.key for shelly in config.shellys),
        dt_util.get_default_time_zone(),
    )
    for warning in stored.warnings:
        _LOGGER.warning("%s", warning)
    controller = FloorHeatingController(hass, config, store, stored)
    notifier = Notifier(hass, config.notify)
    entry.async_on_unload(controller.async_add_event_handler(notifier.async_handle))
    heartbeat = HeartbeatClient(hass, controller)
    watchdog = WatchdogPing(hass, controller)
    entry.runtime_data = FloorHeatingRuntime(controller, heartbeat, watchdog)
    _async_remove_stale_devices(hass, entry, config)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_started(_hass: HomeAssistant) -> None:
        notifier.async_check_targets()
        await controller.async_start()
        heartbeat.async_start()
        watchdog.async_start()

    async def _async_stop(_event: Event) -> None:
        await _async_shutdown(entry.runtime_data)

    entry.async_on_unload(async_at_started(hass, _async_started))
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FloorHeatingConfigEntry) -> bool:
    """Stop the control, then remove the entities. The stored data stays."""
    await _async_shutdown(entry.runtime_data)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_shutdown(runtime: FloorHeatingRuntime) -> None:
    await runtime.heartbeat.async_stop()
    await runtime.watchdog.async_stop()
    await runtime.controller.async_stop()  # writes the stored data


def _async_remove_stale_devices(
    hass: HomeAssistant, entry: FloorHeatingConfigEntry, config: FloorHeatingConfig
) -> None:
    """Remove the devices (and their entities) of zones no longer in the YAML."""
    registry = dr.async_get(hass)
    wanted = {(DOMAIN, GLOBAL_DEVICE)} | {(DOMAIN, zone.id) for zone in config.core.zones}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_remove_device(device.id)
