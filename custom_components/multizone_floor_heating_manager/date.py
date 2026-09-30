"""Schedule form date: the day of a one-shot schedule (docs/design.md §5.3, D-74)."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .entity import FormEntity
from .runtime import FloorheatConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ScheduleDate(entry.runtime_data.controller)])


class ScheduleDate(FormEntity, DateEntity):
    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "schedule_date")

    @property
    def native_value(self) -> date:
        return self.controller.form.day

    async def async_set_value(self, value: date) -> None:
        self.controller.form.day = value
        self.async_write_ha_state()
