"""Parameter number entities.

Per zone: Hysteresis, WaitTime and HolidayTemp. Global: every global
number parameter. Ranges and steps
come from the core's `ParamSpec`s; HA rejects values outside the range (e.g. HpMinOnTime
20 min).

Units: absolute temperatures are °C and converted by HA; temperature differences
are shown in HA's unit system and converted here (HA converts only absolute ones);
durations in minutes or hours.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorHeatingController
from .core.config import GLOBAL_PARAM_SPECS, ZONE_PARAM_SPECS, ParamSpec, ParamUnit, ZoneConfig
from .core.schedule import SCHEDULE_TEMPERATURE_SPEC
from .core.units import TemperatureUnit, delta_from_celsius, delta_to_celsius
from .entity import FloorHeatingEntity, FormEntity, async_apply
from .runtime import FloorHeatingConfigEntry

# Parameter keys; the names are translations (translations/en.json).
GLOBAL_KEYS = (
    "hp_min_on_time",
    "hp_min_off_time",
    "sensor_fault_timeout",
    "manual_max_temp",
    "manual_resume_delta",
    "failsafe_trigger",
    "valve_exercise_duration",
    "long_run_alarm",
)
ZONE_KEYS = ("hysteresis", "wait_time", "holiday_temp")

_TIME_UNITS = {ParamUnit.MINUTES: UnitOfTime.MINUTES, ParamUnit.HOURS: UnitOfTime.HOURS}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    entities = [
        ParamNumber(controller, ZONE_PARAM_SPECS[key], unit, zone)
        for zone in controller.config.core.zones
        for key in ZONE_KEYS
    ]
    entities += [
        ParamNumber(controller, GLOBAL_PARAM_SPECS[key], unit, None) for key in GLOBAL_KEYS
    ]
    async_add_entities([*entities, ScheduleTemperature(controller)])


class ParamNumber(FloorHeatingEntity, NumberEntity):
    """One parameter; a setting (config category)."""

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: FloorHeatingController,
        spec: ParamSpec,
        unit: TemperatureUnit,
        zone: ZoneConfig | None,  # None: a global parameter
    ) -> None:
        super().__init__(controller, spec.key, zone)
        self._spec = spec
        self._unit = unit
        self._zone_id = None if zone is None else zone.id
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


class ScheduleTemperature(FormEntity, NumberEntity):
    """The schedule form's temperature (auto schedules; 10 to 30 °C, converted by HA)."""

    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = SCHEDULE_TEMPERATURE_SPEC.minimum
    _attr_native_max_value = SCHEDULE_TEMPERATURE_SPEC.maximum
    _attr_native_step = SCHEDULE_TEMPERATURE_SPEC.step

    def __init__(self, controller: FloorHeatingController) -> None:
        super().__init__(controller, "schedule_temperature")

    @property
    def native_value(self) -> float:
        return self.controller.form.temperature

    async def async_set_native_value(self, value: float) -> None:
        self.controller.form.temperature = round(value, 2)
        self.async_write_ha_state()
