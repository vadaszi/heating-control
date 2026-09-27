"""Sensors (docs/design.md §5.3): per zone state, reason and effective SetPoint; the
mode sensor with the shadow attribute (D-79); the alerts sensor (count + list)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_CONTROLLER
from .controller import FloorheatController
from .core.config import ZoneConfig
from .core.state import ZoneMode
from .entity import FloorheatEntity

MODES = ["normal", "holiday", "failsafe"]  # holiday from v1.1, failsafe from v1.2


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    controller = hass.data[DATA_CONTROLLER]
    entities: list[SensorEntity] = []
    for zone in controller.config.core.zones:
        entities += [
            ZoneStateSensor(controller, zone),
            ZoneReasonSensor(controller, zone),
            ZoneSetpointSensor(controller, zone),
        ]
    entities += [ModeSensor(controller), AlertsSensor(controller)]
    async_add_entities(entities)


class _ZoneSensor(FloorheatEntity, SensorEntity):
    def __init__(
        self, controller: FloorheatController, zone: ZoneConfig, key: str, name: str
    ) -> None:
        super().__init__(controller, "sensor", f"{zone.id}_{key}", f"{zone.name} {name}")
        self._zone_id = zone.id


class ZoneStateSensor(_ZoneSensor):
    """IDLE / WAITING / HEATING / FORCED / SENSOR_FAULT (§3.2)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [mode.value for mode in ZoneMode]  # noqa: RUF012 - HA's attribute convention

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "state", "state")

    @property
    def native_value(self) -> str:
        return self.controller.state.zones[self._zone_id].mode.value


class ZoneReasonSensor(_ZoneSensor):
    """Why the zone is in its state (D-89), e.g. "Waiting, 12 min left"."""

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "reason", "reason")

    @property
    def native_value(self) -> str | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.zones[self._zone_id].reason


class ZoneSetpointSensor(_ZoneSensor):
    """Effective SetPoint (§3.4); °C, converted by HA (D-77)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "setpoint", "setpoint")

    @property
    def native_value(self) -> float | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.zones[self._zone_id].setpoint


class ModeSensor(FloorheatEntity, SensorEntity):
    """normal / holiday / failsafe; shadow mode is the `shadow` attribute (D-79)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MODES

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "sensor", "mode", "Floorheat mode")

    @property
    def native_value(self) -> str:
        return "normal"  # holiday: v1.1 (P10), failsafe: v1.2 (P11)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"shadow": not self.controller.settings.control_active}


class AlertsSensor(FloorheatEntity, SensorEntity):
    """Number of active alerts; the list is the `alerts` attribute."""

    _attr_icon = "mdi:alert"

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "sensor", "alerts", "Floorheat alerts")

    @property
    def native_value(self) -> int:
        return len(self.controller.alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alerts": [
                {"kind": alert.kind.value, "zone_id": alert.zone_id, "message": alert.message}
                for alert in self.controller.alerts
            ]
        }
