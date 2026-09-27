"""Floor Heating Zone Control (floorheat) integration.

Set up from YAML (docs/design.md §5.6, docs/configuration.md). The control logic lives
in `core/` (no HA imports); this package is the thin HA adapter around it.
"""

from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import DATA_CONTROLLER, DOMAIN
from .controller import FloorheatController
from .core.config import ConfigError, config_warnings
from .core.units import TemperatureUnit
from .schema import CONFIG_SCHEMA, build_config
from .storage import FloorheatStore

__all__ = ["CONFIG_SCHEMA", "DOMAIN", "async_setup"]

_LOGGER = logging.getLogger(__name__)


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
    stored = await store.async_load(floorheat_config.core, floorheat_config.switches)
    for warning in stored.warnings:
        _LOGGER.warning("%s", warning)
    controller = FloorheatController(hass, floorheat_config, store, stored)
    hass.data[DATA_CONTROLLER] = controller

    async def _async_started(_hass: HomeAssistant) -> None:
        await controller.async_start()

    async def _async_stop(_event: Event) -> None:
        await controller.async_stop()

    async_at_started(hass, _async_started)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    return True
