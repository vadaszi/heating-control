"""Base class of floorheat's entities (docs/design.md §5.3, docs/configuration.md).

Entities are views of the controller (D-106): they show its state after every reconcile
run and change its settings. Entity ids are fixed and based on the zone id, unique ids
likewise (D-76, D-115): `<platform>.floorheat_<key>`, unique id `floorheat_<key>`.
"""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import FloorheatController
from .core.config import ConfigError


class FloorheatEntity(Entity):
    """Updated by the controller after every reconcile run; never polled."""

    _attr_should_poll = False
    _attr_has_entity_name = False  # YAML platforms have no device to name it after

    def __init__(self, controller: FloorheatController, platform: str, key: str, name: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self.entity_id = f"{platform}.{DOMAIN}_{key}"

    @property
    def available(self) -> bool:
        """Unavailable until the loop has run once (it starts when HA has started)."""
        return self.controller.outputs is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_add_listener(self.async_write_ha_state))


async def async_apply(change: Awaitable[None]) -> None:
    """Run a settings change; invalid values become a service validation error."""
    try:
        await change
    except ConfigError as err:
        raise ServiceValidationError("; ".join(err.errors)) from err
