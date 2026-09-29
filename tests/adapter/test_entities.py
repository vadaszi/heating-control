"""Entities (docs/design.md §5.3; D-106, D-114 to D-116)."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.floorheat.number import GLOBAL_NAMES

from .conftest import HEAT_SOURCE, World, make_conf, valve
from .test_restart import restarted

ZONE_ENTITIES = [
    "climate.floorheat_{z}",
    "sensor.floorheat_{z}_state",
    "sensor.floorheat_{z}_reason",
    "sensor.floorheat_{z}_setpoint",
    "number.floorheat_{z}_hysteresis",
    "number.floorheat_{z}_wait_time",
]
GLOBAL_ENTITIES = [
    "binary_sensor.floorheat_heat_request",
    "sensor.floorheat_mode",
    "sensor.floorheat_alerts",
    "switch.floorheat_heating_season",
    "switch.floorheat_control_active",
    "time.floorheat_sensor_fault_reminder",
    *(f"number.floorheat_{key}" for key in GLOBAL_NAMES),
]


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def _call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: object
) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)
    await hass.async_block_till_done()


async def test_entities_have_fixed_ids(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    registry = er.async_get(hass)
    expected = [e.format(z=z) for z in ("zone_1", "zone_2") for e in ZONE_ENTITIES]
    expected += GLOBAL_ENTITIES
    for entity_id in expected:
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.platform == "floorheat"
        assert entry.unique_id == "floorheat_" + entity_id.split(".floorheat_")[1]
        assert _state(hass, entity_id).state != "unavailable", entity_id
    assert len(GLOBAL_NAMES) == 9  # every §4 global parameter (D-114)
    assert _state(hass, "climate.floorheat_zone_1").name == "Zone 1"
    assert _state(hass, "sensor.floorheat_zone_2_reason").name == "Zone 2 reason"


async def test_views_are_unavailable_before_the_first_run(
    world: World, hass: HomeAssistant
) -> None:
    from homeassistant.core import CoreState

    hass.set_state(CoreState.starting)
    world.setup_entities()
    assert await world.setup(live=False)
    assert _state(hass, "climate.floorheat_zone_1").state == "unavailable"
    assert _state(hass, "binary_sensor.floorheat_heat_request").state == "unavailable"
    # settings can be changed at once
    assert _state(hass, "switch.floorheat_control_active").state == "off"
    assert float(_state(hass, "number.floorheat_hp_min_on_time").state) == 60


async def test_zone_views_follow_the_core(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await hass.async_block_till_done()
    assert _state(hass, "sensor.floorheat_zone_1_state").state == "waiting"
    reason = _state(hass, "sensor.floorheat_zone_1_reason")
    assert reason.state == "Waiting"  # fixed text; the timer end is `until` (D-123)
    assert reason.attributes["until"] == "2026-01-12T06:30:00+00:00"
    assert _state(hass, "sensor.floorheat_zone_1_setpoint").state == "22.0"
    climate = _state(hass, "climate.floorheat_zone_1")
    assert climate.state == "heat"
    assert climate.attributes["current_temperature"] == 21.8
    assert climate.attributes["temperature"] == 22.0
    assert climate.attributes["hvac_action"] == "idle"
    assert climate.attributes["hvac_modes"] == ["heat"]
    assert climate.attributes["zone_state"] == "waiting"
    request = _state(hass, "binary_sensor.floorheat_heat_request")
    assert request.state == "off"
    assert "on_since" not in request.attributes  # left out while not running (D-123)
    assert "on_duration" not in request.attributes

    waiting_since = reason.last_updated
    await world.advance(10)
    reason = _state(hass, "sensor.floorheat_zone_1_reason")
    assert reason.last_updated == waiting_since  # no per-minute change (D-123)
    await world.advance_to("06:30")
    reason = _state(hass, "sensor.floorheat_zone_1_reason")
    assert reason.state == "Calling zone"
    assert "until" not in reason.attributes
    climate = _state(hass, "climate.floorheat_zone_1")
    assert climate.attributes["hvac_action"] == "heating"
    assert climate.attributes["valve"] is True
    assert climate.attributes["calling_zone"] is True
    assert _state(hass, "climate.floorheat_zone_2").attributes["hvac_action"] == "idle"
    await world.advance(10)
    request = _state(hass, "binary_sensor.floorheat_heat_request")
    assert request.state == "on"
    assert request.attributes["on_since"] == "2026-01-12T06:30:00+00:00"
    assert request.attributes["on_duration"] == 10


async def test_unvalved_zone_heats_while_the_request_is_on(
    world: World, hass: HomeAssistant
) -> None:
    world.setup_entities()
    assert await world.setup(make_conf(2, unvalved=(2,)))
    world.temp(1, 21.8)
    await world.advance_to("06:30")
    climate = _state(hass, "climate.floorheat_zone_2")
    assert climate.attributes["hvac_action"] == "heating"  # no valve: always gets flow
    assert climate.attributes["valve"] is None


async def test_mode_sensor_shows_shadow(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    mode = _state(hass, "sensor.floorheat_mode")
    assert mode.state == "normal"
    assert mode.attributes["shadow"] is True
    await _call(hass, "switch", "turn_on", "switch.floorheat_control_active")
    assert _state(hass, "sensor.floorheat_mode").attributes["shadow"] is False


# ---------------------------------------------------------------- climate


async def test_climate_target_changes_base_setpoint(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    await _call(hass, "climate", "set_temperature", "climate.floorheat_zone_1", temperature=23)
    assert world.controller.settings.zone_params["zone_1"].base_setpoint == 23.0
    # 22.0 is at or below the new StartTemp 22.8: the zone calls at once (a run followed)
    assert world.mode(1) == "heating"  # a SetPoint raise skips WaitTime (D-26)
    assert _state(hass, "climate.floorheat_zone_1").attributes["temperature"] == 23.0
    assert _state(hass, "sensor.floorheat_zone_1_setpoint").state == "23.0"


async def test_climate_rejects_out_of_range_and_other_modes(
    world: World, hass: HomeAssistant
) -> None:
    world.setup_entities()
    assert await world.setup()
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_temperature", "climate.floorheat_zone_1", temperature=31)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_hvac_mode", "climate.floorheat_zone_1", hvac_mode="off")
    await _call(hass, "climate", "set_hvac_mode", "climate.floorheat_zone_1", hvac_mode="heat")
    assert world.controller.settings.zone_params["zone_1"].base_setpoint == 22.0


async def test_climate_in_fahrenheit(world: World, hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    world.setup_entities()
    assert await world.setup()
    climate = _state(hass, "climate.floorheat_zone_1")
    assert climate.attributes["temperature"] == pytest.approx(71.6)
    assert climate.attributes["min_temp"] == pytest.approx(50)
    await _call(hass, "climate", "set_temperature", "climate.floorheat_zone_1", temperature=73.4)
    assert world.controller.settings.zone_params["zone_1"].base_setpoint == pytest.approx(23.0)


# ---------------------------------------------------------------- numbers and time


async def test_global_number_range_is_enforced(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    number = "number.floorheat_hp_min_on_time"
    attributes = _state(hass, number).attributes
    assert (attributes["min"], attributes["max"], attributes["step"]) == (30, 180, 5)
    assert attributes["unit_of_measurement"] == "min"
    with pytest.raises(ServiceValidationError):
        await _call(hass, "number", "set_value", number, value=20)  # never below 30 (D-81)
    await _call(hass, "number", "set_value", number, value=90)
    params = world.controller.settings.global_params
    assert params.hp_min_on_time == timedelta(minutes=90)
    assert float(_state(hass, number).state) == 90


async def test_every_global_parameter_reaches_the_core(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    values = {
        "hp_min_off_time": (45, timedelta(minutes=45)),
        "sensor_fault_timeout": (30, timedelta(minutes=30)),
        "manual_max_temp": (26.5, 26.5),
        "manual_resume_delta": (1.5, 1.5),
        "holiday_temp": (16, 16.0),
        "failsafe_trigger": (12, timedelta(hours=12)),
        "valve_exercise_duration": (10, timedelta(minutes=10)),
        "long_run_alarm": (8, timedelta(hours=8)),
    }
    for key, (value, _) in values.items():
        await _call(hass, "number", "set_value", f"number.floorheat_{key}", value=value)
    params = world.controller.settings.global_params
    for key, (_, expected) in values.items():
        assert getattr(params, key) == expected, key


async def test_zone_numbers(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    await _call(hass, "number", "set_value", "number.floorheat_zone_2_wait_time", value=15)
    await _call(hass, "number", "set_value", "number.floorheat_zone_2_hysteresis", value=0.4)
    params = world.controller.settings.zone_params["zone_2"]
    assert params.wait_time == timedelta(minutes=15)
    assert params.hysteresis == pytest.approx(0.4)
    assert world.controller.settings.zone_params["zone_1"].wait_time == timedelta(minutes=30)
    hysteresis = _state(hass, "number.floorheat_zone_2_hysteresis")
    assert hysteresis.state == "0.4"
    assert hysteresis.attributes["unit_of_measurement"] == "°C"


async def test_temperature_numbers_in_fahrenheit(world: World, hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    world.setup_entities()
    assert await world.setup()
    hysteresis = _state(hass, "number.floorheat_zone_1_hysteresis")
    assert hysteresis.attributes["unit_of_measurement"] == "°F"
    assert float(hysteresis.state) == pytest.approx(0.36)
    assert hysteresis.attributes["min"] == pytest.approx(0.18)
    assert hysteresis.attributes["max"] == pytest.approx(1.8)
    await _call(hass, "number", "set_value", "number.floorheat_zone_1_hysteresis", value=0.9)
    assert world.controller.settings.zone_params["zone_1"].hysteresis == pytest.approx(0.5)
    holiday = _state(hass, "number.floorheat_holiday_temp")
    assert holiday.attributes["unit_of_measurement"] == "°F"
    assert float(holiday.state) == pytest.approx(64.4)
    await _call(hass, "number", "set_value", "number.floorheat_holiday_temp", value=59)
    assert world.controller.settings.global_params.holiday_temp == pytest.approx(15.0)


async def test_reminder_time(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    entity_id = "time.floorheat_sensor_fault_reminder"
    assert _state(hass, entity_id).state == "08:00:00"
    await _call(hass, "time", "set_value", entity_id, time="07:30:15")
    assert world.controller.settings.global_params.sensor_fault_reminder == time(7, 30)
    assert _state(hass, entity_id).state == "07:30:00"


async def test_entity_settings_survive_a_restart(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    await _call(hass, "number", "set_value", "number.floorheat_hp_min_off_time", value=120)
    await _call(hass, "climate", "set_temperature", "climate.floorheat_zone_2", temperature=20.5)
    await _call(hass, "switch", "turn_off", "switch.floorheat_heating_season")

    async with restarted(world, prepare=lambda new: new.setup_entities()) as new:
        assert await new.setup(make_conf(), live=False)
        assert float(_state(new.hass, "number.floorheat_hp_min_off_time").state) == 120
        climate = _state(new.hass, "climate.floorheat_zone_2")
        assert climate.attributes["temperature"] == 20.5
        assert _state(new.hass, "switch.floorheat_heating_season").state == "off"
        assert _state(new.hass, "switch.floorheat_control_active").state == "on"


# ---------------------------------------------------------------- switches


async def test_control_active_switch_off_sends_the_final_safe_set(
    world: World, hass: HomeAssistant
) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:30")
    world.calls.clear()
    await _call(hass, "switch", "turn_off", "switch.floorheat_control_active")
    assert sorted(world.calls) == [(HEAT_SOURCE, "off"), (valve(1), "off")]
    assert _state(hass, "switch.floorheat_control_active").state == "off"


async def test_heating_season_switch_stops_the_request(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:40")
    assert world.state(HEAT_SOURCE) == "on"
    await _call(hass, "switch", "turn_off", "switch.floorheat_heating_season")
    assert world.state(HEAT_SOURCE) == "off"  # at once, min ON ignored (D-68)
    assert world.state(valve(1)) == "off"
    assert _state(hass, "sensor.floorheat_zone_1_reason").state == "Heating season off"
    assert _state(hass, "binary_sensor.floorheat_heat_request").state == "off"


# ---------------------------------------------------------------- trial with stand-ins


async def test_shadow_trial_with_stand_in_switches(world: World, hass: HomeAssistant) -> None:
    """D-113: real sensors, stand-in switches, no Shellys; a simulated day in shadow mode."""
    world.setup_entities(3)
    assert await world.setup(make_conf(3, unvalved=(3,)), live=False)
    world.temp(2, 21.7)
    await world.advance_to("12:00")
    world.temp(2, 22.2)
    world.temp(1, 21.8)
    await world.advance_to("23:00")
    assert world.calls == []  # shadow mode: the stand-ins are never switched
    assert {s.state for s in hass.states.async_all("switch") if "valve" in s.entity_id} == {"off"}
    history = world.controller.state
    assert history.hp_last_on_at is not None  # the simulated heat pump ran
    assert _state(hass, "sensor.floorheat_alerts").state == "0"

    await _call(hass, "switch", "turn_on", "switch.floorheat_control_active")
    world.temp(1, 21.5)
    await world.advance(90)
    assert (valve(1), "on") in world.calls  # live: the stand-ins now follow floorheat
    assert world.state(valve(1)) == "on"


async def test_real_template_switches_as_stand_ins(world: World, hass: HomeAssistant) -> None:
    """D-113 with HA's own Template switches (no state template: optimistic)."""
    names = ("stand_in_heat_source", "stand_in_valve_1", "stand_in_valve_2")
    assert await async_setup_component(
        hass,
        "template",
        {"template": [{"switch": [{"name": name, "unique_id": name} for name in names]}]},
    )
    await hass.async_block_till_done()
    world.temp(1, 22.0)
    world.temp(2, 22.0)
    conf = make_conf(2)
    conf["floorheat"]["heat_source_switch"] = "switch.stand_in_heat_source"
    for n, zone in enumerate(conf["floorheat"]["zones"], start=1):
        zone["valve"] = f"switch.stand_in_valve_{n}"
    assert await world.setup(conf)
    world.temp(1, 21.0)
    await world.advance(40)
    # A new Template switch is `unknown` until switched once; floorheat treats unknown
    # like unavailable and sends nothing (D-66, D-108), so the docs say: switch each
    # stand-in OFF once after creating it.
    assert _state(hass, "switch.stand_in_valve_1").state == "unknown"
    for name in names:
        await _call(hass, "switch", "turn_off", f"switch.{name}")
    await world.advance(1)
    assert _state(hass, "switch.stand_in_valve_1").state == "on"
    assert _state(hass, "switch.stand_in_valve_2").state == "off"
    assert _state(hass, "sensor.floorheat_zone_1_state").state == "heating"
