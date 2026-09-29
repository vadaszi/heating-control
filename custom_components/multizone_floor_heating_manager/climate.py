"""Zone climate entities (docs/design.md §5.3).

Current temperature = RoomTemp, target = BaseSetPoint, `heat` mode only (no per-zone
OFF). The entity works in °C; HA converts to and from its unit system (D-77).
`hvac_action` is heating while the heat source request is ON and the zone gets flow:
its valve is open, or it has no valve (D-116).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .core.config import ZONE_PARAM_SPECS, ZoneConfig
from .entity import FloorheatEntity, async_apply
from .runtime import FloorheatConfigEntry

_SETPOINT = ZONE_PARAM_SPECS["base_setpoint"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    async_add_entities(ZoneClimate(controller, zone) for zone in controller.config.core.zones)


class ZoneClimate(FloorheatEntity, ClimateEntity):
    """One zone's thermostat."""

    _attr_hvac_modes = [HVACMode.HEAT]  # noqa: RUF012 - HA's attribute convention
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = _SETPOINT.step
    _attr_min_temp = _SETPOINT.minimum
    _attr_max_temp = _SETPOINT.maximum
    _attr_name = None  # the zone device's main entity: named like the device (D-125)
    _attr_translation_key = "zone"  # translates the attribute values

    def __init__(self, controller: FloorheatController, zone: ZoneConfig) -> None:
        super().__init__(controller, "climate", zone)
        self._attr_unique_id = zone.id
        self._zone = zone

    @property
    def current_temperature(self) -> float | None:
        outputs = self.controller.outputs
        return None if outputs is None else outputs.zones[self._zone.id].room_temp

    @property
    def target_temperature(self) -> float:
        return self.controller.settings.zone_params[self._zone.id].base_setpoint

    @property
    def hvac_action(self) -> HVACAction:
        outputs = self.controller.outputs
        if outputs is None or not outputs.heat_source_on:
            return HVACAction.IDLE
        flow = outputs.valves.get(self._zone.id, True)  # no valve: always open
        return HVACAction.HEATING if flow else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outputs = self.controller.outputs
        state = self.controller.state
        return {
            "zone_state": state.zones[self._zone.id].mode.value,
            "reason": None if outputs is None else outputs.zones[self._zone.id].reason.value,
            "valve": None if outputs is None else outputs.valves.get(self._zone.id),
            "calling_zone": state.calling_zone == self._zone.id,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await async_apply(
            self.controller.async_set_zone_params(self._zone.id, base_setpoint=float(temperature))
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.HEAT:
            raise ServiceValidationError("Floor heating zones support the heat mode only")
