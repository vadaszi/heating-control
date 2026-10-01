"""Date entities: the schedule form's day of a one-shot schedule (docs/design.md §5.3,
D-74) and the holiday end date (§3.4, D-142; empty = no end, cleared when holiday ends)."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity
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
    controller = entry.runtime_data.controller
    async_add_entities([ScheduleDate(controller), HolidayEndDate(controller)])


class ScheduleDate(FormEntity, DateEntity):
    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "schedule_date")

    @property
    def native_value(self) -> date:
        return self.controller.form.day

    async def async_set_value(self, value: date) -> None:
        self.controller.form.day = value
        self.async_write_ha_state()


class HolidayEndDate(FormEntity, DateEntity):
    """With the holiday end time, the end of holiday; empty (unknown) = no end (D-142).
    Not checked when set: switching holiday on checks the end (D-137)."""

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "holiday_end_date")

    @property
    def native_value(self) -> date | None:
        return self.controller.settings.holiday_end_date

    async def async_set_value(self, value: date) -> None:
        await async_apply(self.controller.async_set_holiday_end_date(value))
        self.async_write_ha_state()
