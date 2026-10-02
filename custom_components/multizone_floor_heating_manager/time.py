"""Time entities (local times): the time-of-day parameters of §4 — SensorFaultReminder
(§3.6), the failsafe window start and end (D-147) and the valve exercise time (D-149);
the schedule form's start and end (§5.3, D-74); the holiday end time (§3.4, D-142)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .core.config import TIME_OF_DAY_PARAMS
from .entity import FloorheatEntity, FormEntity, async_apply
from .runtime import FloorheatConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
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


class ParamTime(FloorheatEntity, TimeEntity):
    """A global time-of-day parameter; a setting (config category)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: FloorheatController, key: str) -> None:
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

    def __init__(self, controller: FloorheatController, key: str) -> None:
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
    """With the holiday end date, the end of holiday (local time, D-142). Never empty; it
    keeps its value when holiday ends."""

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "holiday_end_time")

    @property
    def native_value(self) -> time:
        return self.controller.settings.holiday_end_time

    async def async_set_value(self, value: time) -> None:
        await async_apply(self.controller.async_set_holiday_end_time(value))
        self.async_write_ha_state()
