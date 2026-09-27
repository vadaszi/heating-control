"""Parameter number entities (docs/design.md §4, §5.3).

Per zone: Hysteresis and WaitTime. Global: every §4 global parameter (D-114), including
the ones whose features follow in v1.1/v1.2. Ranges and steps come from the core's
`ParamSpec`s; HA rejects values outside the range (e.g. HpMinOnTime 20 min, D-81).

Units (D-77): absolute temperatures are °C and converted by HA; temperature differences
are shown in HA's unit system and converted here (HA converts only absolute ones);
durations in minutes or hours.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_CONTROLLER
from .controller import FloorheatController
from .core.config import GLOBAL_PARAM_SPECS, ZONE_PARAM_SPECS, ParamSpec, ParamUnit
from .core.units import TemperatureUnit, delta_from_celsius, delta_to_celsius
from .entity import FloorheatEntity, async_apply

GLOBAL_NAMES = {
    "hp_min_on_time": "HP min ON time",
    "hp_min_off_time": "HP min OFF time",
    "sensor_fault_timeout": "sensor fault timeout",
    "manual_max_temp": "manual max temperature",
    "manual_resume_delta": "manual resume delta",
    "holiday_temp": "holiday temperature",
    "failsafe_trigger": "failsafe trigger",
    "valve_exercise_duration": "valve exercise duration",
    "long_run_alarm": "long run alarm",
}
ZONE_NAMES = {"hysteresis": "hysteresis", "wait_time": "wait time"}

_TIME_UNITS = {ParamUnit.MINUTES: UnitOfTime.MINUTES, ParamUnit.HOURS: UnitOfTime.HOURS}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    controller = hass.data[DATA_CONTROLLER]
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    entities = [
        ParamNumber(controller, ZONE_PARAM_SPECS[key], unit, zone.id, f"{zone.name} {name}")
        for zone in controller.config.core.zones
        for key, name in ZONE_NAMES.items()
    ]
    entities += [
        ParamNumber(controller, GLOBAL_PARAM_SPECS[key], unit, None, f"Floorheat {name}")
        for key, name in GLOBAL_NAMES.items()
    ]
    async_add_entities(entities)


class ParamNumber(FloorheatEntity, NumberEntity):
    """One §4 parameter."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        controller: FloorheatController,
        spec: ParamSpec,
        unit: TemperatureUnit,
        zone_id: str | None,  # None: a global parameter
        name: str,
    ) -> None:
        key = spec.key if zone_id is None else f"{zone_id}_{spec.key}"
        super().__init__(controller, "number", key, name)
        self._spec = spec
        self._unit = unit
        self._zone_id = zone_id
        self._attr_native_step = spec.step
        if spec.unit is ParamUnit.CELSIUS:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif spec.unit is ParamUnit.CELSIUS_DELTA:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE_DELTA
            self._attr_native_unit_of_measurement = unit.value
        else:
            self._attr_device_class = NumberDeviceClass.DURATION
            self._attr_native_unit_of_measurement = _TIME_UNITS[spec.unit]
        self._attr_native_min_value = self._shown(spec.minimum)
        self._attr_native_max_value = self._shown(spec.maximum)

    @property
    def available(self) -> bool:
        return True  # a setting: changeable even before the first run

    def _shown(self, number: float) -> float:
        """A number in the spec's unit as shown (deltas in HA's unit system)."""
        if self._spec.unit is ParamUnit.CELSIUS_DELTA:
            return round(delta_from_celsius(number, self._unit), 6)
        return number

    @property
    def native_value(self) -> float:
        settings = self.controller.settings
        params = (
            settings.global_params if self._zone_id is None else settings.zone_params[self._zone_id]
        )
        return self._shown(self._spec.to_number(getattr(params, self._spec.key)))

    async def async_set_native_value(self, value: float) -> None:
        number = (
            delta_to_celsius(value, self._unit)
            if self._spec.unit is ParamUnit.CELSIUS_DELTA
            else value
        )
        change = {self._spec.key: self._spec.from_number(number)}
        if self._zone_id is None:
            await async_apply(self.controller.async_set_global_params(**change))
        else:
            await async_apply(self.controller.async_set_zone_params(self._zone_id, **change))
        self.async_write_ha_state()
