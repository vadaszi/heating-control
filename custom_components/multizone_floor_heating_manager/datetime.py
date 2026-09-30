"""Holiday end (docs/design.md §3.4, D-79, D-137): empty until set; setting it while
holiday runs moves the end; it is cleared when holiday ends."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .entity import FormEntity, async_apply
from .runtime import FloorheatConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HolidayEnd(entry.runtime_data.controller)])


class HolidayEnd(FormEntity, DateTimeEntity):
    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "holiday_end")

    @property
    def native_value(self) -> datetime | None:
        return self.controller.settings.holiday_end

    async def async_set_value(self, value: datetime) -> None:
        await async_apply(self.controller.async_set_holiday_end(value))
        self.async_write_ha_state()
