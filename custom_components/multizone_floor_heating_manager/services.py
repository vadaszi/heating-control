"""Schedule services (docs/design.md §5.3, D-139): add, delete and list schedules.

They act on the loaded config entry and use the same validated controller methods as
the schedule form (D-74). A rejected schedule raises a `ServiceValidationError` naming
the problem, and nothing is stored (D-19, A12). Zones are given by their YAML ids, or
`all` (D-139); temperatures are in HA's unit system (D-77).
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .controller import FloorheatController
from .core.schedule import ScheduleKind
from .core.units import TemperatureUnit, to_celsius
from .entity import async_apply
from .runtime import FloorheatConfigEntry
from .schedules import WEEKDAY_KEYS, schedule_view

SERVICE_ADD = "add_schedule"
SERVICE_DELETE = "delete_schedule"
SERVICE_LIST = "list_schedules"

ALL_ZONES = "all"

ADD_SCHEMA = vol.Schema(
    {
        vol.Required("kind"): vol.In([kind.value for kind in ScheduleKind]),
        vol.Required("zones"): vol.All(cv.ensure_list, [cv.string]),
        vol.Exclusive("date", "days"): cv.date,
        vol.Exclusive("weekdays", "days"): vol.All(cv.ensure_list, [vol.In(WEEKDAY_KEYS)]),
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Optional("temperature"): vol.Coerce(float),
    }
)
DELETE_SCHEMA = vol.Schema({vol.Required("schedule_id"): cv.string})


def async_register(hass: HomeAssistant) -> None:
    """Register the services (once, in `async_setup`)."""

    async def add(call: ServiceCall) -> ServiceResponse:
        controller = _controller(hass)
        data = call.data
        unit = _unit(hass)
        zones: list[str] = data["zones"]
        temperature = data.get("temperature")
        schedule = await async_apply(
            controller.async_add_schedule(
                kind=ScheduleKind(data["kind"]),
                zone_ids=None if zones == [ALL_ZONES] else tuple(zones),
                start=data["start"],
                end=data["end"],
                on_date=data.get("date"),
                weekdays=frozenset(WEEKDAY_KEYS.index(day) for day in data.get("weekdays", [])),
                temperature=None
                if temperature is None
                else round(to_celsius(temperature, unit), 2),
            )
        )
        return {"schedule": schedule_view(schedule, controller.config.core, unit)}

    async def delete(call: ServiceCall) -> None:
        schedule_id = call.data["schedule_id"].strip().removeprefix("#")
        await async_apply(_controller(hass).async_delete_schedule(schedule_id))

    async def list_(call: ServiceCall) -> ServiceResponse:
        controller = _controller(hass)
        unit = _unit(hass)
        return {
            "schedules": [
                schedule_view(schedule, controller.config.core, unit)
                for schedule in controller.settings.schedules
            ]
        }

    hass.services.async_register(
        DOMAIN, SERVICE_ADD, add, ADD_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, SERVICE_DELETE, delete, DELETE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_LIST, list_, vol.Schema({}), supports_response=SupportsResponse.ONLY
    )


def _controller(hass: HomeAssistant) -> FloorheatController:
    entries: list[FloorheatConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Multizone Floor Heating Manager is not loaded")
    return entries[0].runtime_data.controller


def _unit(hass: HomeAssistant) -> TemperatureUnit:
    return TemperatureUnit(hass.config.units.temperature_unit)
