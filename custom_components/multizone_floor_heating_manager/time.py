"""SensorFaultReminder: the local time of the daily sensor fault reminder (§3.6, §4)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .entity import FloorheatEntity, async_apply
from .runtime import FloorheatConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ReminderTime(entry.runtime_data.controller)])


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
