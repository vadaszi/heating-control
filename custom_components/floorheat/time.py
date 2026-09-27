"""SensorFaultReminder: the local time of the daily sensor fault reminder (§3.6, §4)."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_CONTROLLER
from .controller import FloorheatController
from .entity import FloorheatEntity, async_apply


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([ReminderTime(hass.data[DATA_CONTROLLER])])


class ReminderTime(FloorheatEntity, TimeEntity):
    _attr_icon = "mdi:bell-ring"

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(
            controller, "time", "sensor_fault_reminder", "Floorheat sensor fault reminder"
        )

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
