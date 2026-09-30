"""Sensors (docs/design.md §5.3): per zone state, reason and effective SetPoint; the
mode sensor with the shadow attribute (D-79); the alerts sensor (count + list); the
schedules sensor (count + list, D-139).

State and reason are enums of fixed keys; their texts are translations (D-126)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .core.config import ZoneConfig
from .core.io import Reason
from .core.state import ZoneMode
from .core.units import TemperatureUnit
from .entity import FloorheatEntity
from .runtime import FloorheatConfigEntry
from .schedules import schedule_view

MODES = ["normal", "holiday", "failsafe"]  # failsafe from v1.2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    entities: list[SensorEntity] = []
    for zone in controller.config.core.zones:
        entities += [
            ZoneStateSensor(controller, zone),
            ZoneReasonSensor(controller, zone),
            ZoneSetpointSensor(controller, zone),
        ]
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    entities += [
        ModeSensor(controller),
        AlertsSensor(controller),
        SchedulesSensor(controller, unit),
    ]
    async_add_entities(entities)


class _ZoneSensor(FloorheatEntity, SensorEntity):
    def __init__(self, controller: FloorheatController, zone: ZoneConfig, key: str) -> None:
        super().__init__(controller, key, zone)
        self._zone_id = zone.id


class ZoneStateSensor(_ZoneSensor):
    """IDLE / WAITING / HEATING / FORCED / SENSOR_FAULT (§3.2)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [mode.value for mode in ZoneMode]  # noqa: RUF012 - HA's attribute convention
    _attr_translation_key = "zone_state"

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "state")

    @property
    def native_value(self) -> str:
        return self.controller.state.zones[self._zone_id].mode.value


class ZoneReasonSensor(_ZoneSensor):
    """Why the zone is in its state (D-89): a fixed key such as `waiting` (D-126). It
    never counts down; the end of the running timer is the `until` attribute, present
    only while one runs (D-123)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [reason.value for reason in Reason]  # noqa: RUF012 - HA's attribute convention

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "reason")

    @property
    def native_value(self) -> str | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.zones[self._zone_id].reason.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outputs = self.controller.outputs
        until = None if outputs is None else outputs.zones[self._zone_id].until
        return {} if until is None else {"until": until.isoformat()}


class ZoneSetpointSensor(_ZoneSensor):
    """Effective SetPoint (§3.4); °C, converted by HA (D-77)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _attr_translation_key = "effective_setpoint"

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, zone, "setpoint")

    @property
    def native_value(self) -> float | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.zones[self._zone_id].setpoint


class ModeSensor(FloorheatEntity, SensorEntity):
    """normal / holiday / failsafe; shadow mode is the `shadow` attribute (D-79)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MODES

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "mode")

    @property
    def native_value(self) -> str:
        outputs = self.controller.outputs  # failsafe: v1.2 (P11)
        return "holiday" if outputs is not None and outputs.holiday_active else "normal"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"shadow": not self.controller.settings.control_active}


class AlertsSensor(FloorheatEntity, SensorEntity):
    """Number of active alerts; the list is the `alerts` attribute."""

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "alerts")

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


class SchedulesSensor(FloorheatEntity, SensorEntity):
    """Number of schedules; the list is the `schedules` attribute (D-139)."""

    def __init__(self, controller: FloorheatController, unit: TemperatureUnit) -> None:
        super().__init__(controller, "schedules")
        self._unit = unit

    @property
    def native_value(self) -> int:
        return len(self.controller.settings.schedules)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        config = self.controller.config.core
        return {
            "schedules": [
                schedule_view(schedule, config, self._unit)
                for schedule in self.controller.settings.schedules
            ]
        }
