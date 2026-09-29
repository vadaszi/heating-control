"""Heating season and Control active switches (docs/design.md §3.7, §5.5).

Their state is the controller's setting (D-106); switching Control active OFF sends the
final safe command set (D-69, D-110).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .entity import FloorheatEntity
from .runtime import FloorheatConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    async_add_entities(
        [
            SettingSwitch(
                controller,
                "heating_season",
                lambda: controller.settings.heating_season,
                controller.async_set_heating_season,
            ),
            SettingSwitch(
                controller,
                "control_active",
                lambda: controller.settings.control_active,
                controller.async_set_control_active,
            ),
        ]
    )


class SettingSwitch(FloorheatEntity, SwitchEntity):
    def __init__(
        self,
        controller: FloorheatController,
        key: str,
        get: Callable[[], bool],
        set_: Callable[[bool], Awaitable[None]],
    ) -> None:
        super().__init__(controller, key)
        self._get = get
        self._set = set_

    @property
    def available(self) -> bool:
        return True  # a setting: changeable even before the first run

    @property
    def is_on(self) -> bool:
        return self._get()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
        self.async_write_ha_state()
