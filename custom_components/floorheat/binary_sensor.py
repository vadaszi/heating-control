"""Heat request binary sensor (docs/design.md §5.3, D-116).

On = the desired heat source request (in shadow mode: the simulated one). `on_since` is
when the heat source (actual, or commanded in shadow mode) last switched ON while it is
running; `on_duration` its running time in minutes, not recorded in the history.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import DATA_CONTROLLER
from .controller import FloorheatController
from .entity import FloorheatEntity


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([HeatRequestSensor(hass.data[DATA_CONTROLLER])])


class HeatRequestSensor(FloorheatEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _unrecorded_attributes = frozenset({"on_duration"})

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "binary_sensor", "heat_request", "Floorheat heat request")

    @property
    def is_on(self) -> bool | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.heat_source_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.controller.state
        since = state.hp_last_on_at if state.hp_actual_on else None
        duration = None if since is None else int((dt_util.utcnow() - since) / timedelta(minutes=1))
        return {
            "on_since": None if since is None else since.isoformat(),
            "on_duration": duration,
        }
