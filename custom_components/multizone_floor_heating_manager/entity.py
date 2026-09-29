"""Base class of the integration's entities (docs/design.md §5.3, docs/configuration.md).

Entities are views of the controller (D-106): they show its state after every reconcile
run and change its settings. They follow HA's naming conventions (D-125): each belongs
to a zone device ("<zone name> floor heating") or to the "Floor heating" device, its
name (a translation) names only the value, and HA generates the entity id from device
name + entity name. Unique ids are built from the zone id and the key (D-76).
"""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, GLOBAL_DEVICE, SHORT_NAME, ZONE_DEVICE_SUFFIX
from .controller import FloorheatController
from .core.config import ConfigError, ZoneConfig


def device_info(zone: ZoneConfig | None) -> DeviceInfo:
    """The zone's device, or the "Floor heating" device for `None`."""
    if zone is None:
        return DeviceInfo(
            identifiers={(DOMAIN, GLOBAL_DEVICE)},
            name=SHORT_NAME,
            entry_type=DeviceEntryType.SERVICE,
        )
    return DeviceInfo(
        identifiers={(DOMAIN, zone.id)},
        name=f"{zone.name} {ZONE_DEVICE_SUFFIX}",
        entry_type=DeviceEntryType.SERVICE,
    )


class FloorheatEntity(Entity):
    """Updated by the controller after every reconcile run; never polled."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self, controller: FloorheatController, key: str, zone: ZoneConfig | None = None
    ) -> None:
        """`key` names the value (also the translation key); `zone` None: a global one."""
        self.controller = controller
        self._attr_unique_id = key if zone is None else f"{zone.id}_{key}"
        self._attr_device_info = device_info(zone)
        if not hasattr(self, "_attr_translation_key"):
            self._attr_translation_key = key

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
