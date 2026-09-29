"""Floor Heating Zone Control (floorheat) integration.

Set up from YAML (docs/design.md §5.6, docs/configuration.md). The control logic lives
in `core/` (no HA imports); this package is the thin HA adapter around it.
"""

from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import DATA_CONTROLLER, DOMAIN
from .controller import FloorheatController
from .core.config import ConfigError, config_warnings
from .core.units import TemperatureUnit
from .heartbeat import HeartbeatClient
from .notifications import Notifier
from .schema import CONFIG_SCHEMA, build_config
from .storage import FloorheatStore

__all__ = ["CONFIG_SCHEMA", "DOMAIN", "async_setup"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up floorheat from `configuration.yaml`."""
    if DOMAIN not in config:
        return True
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    try:
        floorheat_config = build_config(config[DOMAIN], unit)
    except ConfigError as err:
        _LOGGER.error("%s", err)
        return False
    for warning in config_warnings(floorheat_config.core):
        _LOGGER.warning("%s", warning)

    store = FloorheatStore(hass)
    stored = await store.async_load(
        floorheat_config.core,
        floorheat_config.switches,
        tuple(shelly.key for shelly in floorheat_config.shellys),
    )
    for warning in stored.warnings:
        _LOGGER.warning("%s", warning)
    controller = FloorheatController(hass, floorheat_config, store, stored)
    hass.data[DATA_CONTROLLER] = controller
    notifier = Notifier(hass, floorheat_config.notify)
    controller.async_add_event_handler(notifier.async_handle)
    heartbeat = HeartbeatClient(hass, controller)
    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, config), f"{DOMAIN} {platform}"
        )

    async def _async_started(_hass: HomeAssistant) -> None:
        notifier.async_check_targets()
        await controller.async_start()
        heartbeat.async_start()

    async def _async_stop(_event: Event) -> None:
        await heartbeat.async_stop()
        await controller.async_stop()

    async_at_started(hass, _async_started)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    return True
