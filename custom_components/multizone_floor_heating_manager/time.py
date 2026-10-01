"""Time entities: SensorFaultReminder, the local time of the daily sensor fault reminder
(§3.6, §4); the schedule form's start and end (§5.3, D-74); the holiday end time (§3.4,
D-142)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
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
            ReminderTime(controller),
            ScheduleTime(controller, "schedule_start"),
            ScheduleTime(controller, "schedule_end"),
            HolidayEndTime(controller),
        ]
    )


class ReminderTime(FloorheatEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "sensor_fault_reminder")

    @property
    def available(self) -> bool:
        return True  # a setting: changeable even before the first run

    @property
    def native_value(self) -> time:
        return self.controller.settings.global_params.sensor_fault_reminder

    async def async_set_value(self, value: time) -> None:
        reminder = value.replace(second=0, microsecond=0, tzinfo=None)
        await async_apply(self.controller.async_set_global_params(sensor_fault_reminder=reminder))
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
