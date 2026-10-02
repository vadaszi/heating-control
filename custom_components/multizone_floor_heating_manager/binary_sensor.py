"""Heat request binary sensor.

On = the desired heat source request (in shadow mode: the simulated one). While the
heat source (actual, or commanded in shadow mode) runs, `on_since` is when it switched
ON and `on_duration` its running time in minutes, not recorded in the history. Both are
left out while it is not running.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .controller import FloorHeatingController
from .entity import FloorHeatingEntity
from .runtime import FloorHeatingConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HeatRequestSensor(entry.runtime_data.controller)])


class HeatRequestSensor(FloorHeatingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _unrecorded_attributes = frozenset({"on_duration"})

    def __init__(self, controller: FloorHeatingController) -> None:
        super().__init__(controller, "heat_request")

    @property
    def is_on(self) -> bool | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.heat_source_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.controller.state
        since = state.hp_last_on_at if state.hp_actual_on else None
        if since is None:
            return {}
        return {
            "on_since": since.isoformat(),
            "on_duration": int((dt_util.utcnow() - since) / timedelta(minutes=1)),
        }
