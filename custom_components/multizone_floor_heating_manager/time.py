"""Time entities (local times): the time-of-day parameters (SensorFaultReminder, the failsafe
operation start and stop, i.e. FailsafeWindow, and the valve exercise time); the schedule form's
start and end; the holiday end time."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorHeatingController
from .core.config import TIME_OF_DAY_PARAMS
from .entity import FloorHeatingEntity, FormEntity, async_apply
from .runtime import FloorHeatingConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    async_add_entities(
        [
            *(ParamTime(controller, key) for key in TIME_OF_DAY_PARAMS),
            ScheduleTime(controller, "schedule_start"),
            ScheduleTime(controller, "schedule_end"),
            HolidayEndTime(controller),
        ]
    )


class ParamTime(FloorHeatingEntity, TimeEntity):
    """A global time-of-day parameter; a setting (config category)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: FloorHeatingController, key: str) -> None:
        super().__init__(controller, key)
        self._key = key

    @property
    def available(self) -> bool:
        return True  # a setting: changeable even before the first run

    @property
    def native_value(self) -> time:
        value: time = getattr(self.controller.settings.global_params, self._key)
        return value

    async def async_set_value(self, value: time) -> None:
        local = value.replace(second=0, microsecond=0, tzinfo=None)
        await async_apply(self.controller.async_set_global_params(**{self._key: local}))
        self.async_write_ha_state()


class ScheduleTime(FormEntity, TimeEntity):
    """Start or end of the next schedule's window (local time)."""

    def __init__(self, controller: FloorHeatingController, key: str) -> None:
        super().__init__(controller, key)
        self._field = key.removeprefix("schedule_")  # "start" / "end"

    @property
    def native_value(self) -> time:
        value: time = getattr(self.controller.form, self._field)
        return value

    async def async_set_value(self, value: time) -> None:
        setattr(self.controller.form, self._field, value.replace(second=0, microsecond=0))
        self.async_write_ha_state()


class HolidayEndTime(FormEntity, TimeEntity):
    """With the holiday end date, the end of holiday (local time). Never empty; it
    keeps its value when holiday ends."""

    def __init__(self, controller: FloorHeatingController) -> None:
        super().__init__(controller, "holiday_end_time")

    @property
    def native_value(self) -> time:
        return self.controller.settings.holiday_end_time

    async def async_set_value(self, value: time) -> None:
        await async_apply(self.controller.async_set_holiday_end_time(value))
        self.async_write_ha_state()
