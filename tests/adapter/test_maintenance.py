"""Failsafe, valve exercise and long run alarm in HA."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.multizone_floor_heating_manager.const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.multizone_floor_heating_manager.core.io import EventKind
from custom_components.multizone_floor_heating_manager.notifications import TITLES

from .conftest import HEAT_SOURCE, START, World, make_conf, sensor, valve
from .test_restart import restarted

WINDOW_START = "time.floor_heating_failsafe_operation_start"  # FailsafeWindow
WINDOW_END = "time.floor_heating_failsafe_operation_stop"
EXERCISE_DAY = "select.floor_heating_off_season_valve_exercise_day"
EXERCISE_TIME = "time.floor_heating_off_season_valve_exercise_time"


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def _call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: object
) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)
    await hass.async_block_till_done()


def _conf(**extra: Any) -> dict[str, Any]:
    conf = make_conf(2, **extra)
    conf[DOMAIN]["notify"] = ["notify.phone"]
    return conf


# ---------------------------------------------------------------- settings


async def test_failsafe_window_and_exercise_settings(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    assert _state(hass, WINDOW_START).state == "10:00:00"
    assert _state(hass, WINDOW_END).state == "15:00:00"
    assert _state(hass, EXERCISE_DAY).state == "monday"
    assert _state(hass, EXERCISE_TIME).state == "08:00:00"

    await _call(hass, "time", "set_value", WINDOW_START, time="22:00:30")
    await _call(hass, "time", "set_value", WINDOW_END, time="03:00")
    await _call(hass, "select", "select_option", EXERCISE_DAY, option="saturday")
    await _call(hass, "time", "set_value", EXERCISE_TIME, time="09:45")
    params = world.controller.settings.global_params
    assert (params.failsafe_window_start, params.failsafe_window_end) == (time(22), time(3))
    assert (params.valve_exercise_weekday, params.valve_exercise_time) == (5, time(9, 45))

    with pytest.raises(ServiceValidationError, match="start and end must differ"):
        await _call(hass, "time", "set_value", WINDOW_END, time="22:00")
    assert world.controller.settings.global_params.failsafe_window_end == time(3)

    async with restarted(world, prepare=lambda new: new.setup_entities()) as new:
        assert await new.setup(make_conf(), live=False)
        params = new.controller.settings.global_params
        assert (params.failsafe_window_start, params.failsafe_window_end) == (time(22), time(3))
        assert (params.valve_exercise_weekday, params.valve_exercise_time) == (5, time(9, 45))
        assert _state(new.hass, EXERCISE_DAY).state == "saturday"


async def test_unreadable_stored_times_keep_their_defaults(
    world: World, hass_storage: dict[str, Any]
) -> None:
    _preload(
        hass_storage,
        {
            "settings": {
                "global": {
                    "failsafe_window_start": "ten",
                    "valve_exercise_weekday": "monday",
                    "valve_exercise_time": "07:15:00",
                }
            }
        },
    )
    world.setup_entities()
    assert await world.setup(live=False)
    params = world.controller.settings.global_params
    assert params.failsafe_window_start == time(10)
    assert params.valve_exercise_weekday == 0
    assert params.valve_exercise_time == time(7, 15)


def test_every_event_kind_has_a_title() -> None:
    assert set(TITLES) == set(EventKind)
    assert TITLES[EventKind.FAILSAFE_STARTED] == "Floor heating: failsafe started"
    assert TITLES[EventKind.LONG_RUN_ENDED] == "Floor heating: heat source back to normal"


# ---------------------------------------------------------------- failsafe


def _preload(hass_storage: dict[str, Any], data: object) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": data,
    }


def _dead_since(hours: int) -> dict[str, Any]:
    """Stored state: every sensor silent for `hours` (both zones in sensor fault)."""
    last = (START - timedelta(hours=hours)).isoformat()
    zone = {"mode": "sensor_fault", "last_valid_value": 22.0, "last_valid_at": last}
    return {
        "core": {"schema_version": 1, "zones": {"zone_1": zone, "zone_2": zone}},
        "settings": {"shadow_mode": False},
    }


async def test_failsafe_in_ha(
    world: World, hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Every sensor silent for 25 h: failsafe at once, notified, waiting for 10:00; then
    the heat source and every valve run; the first reading ends it."""
    phone = async_mock_service(hass, "notify", "phone")
    _preload(hass_storage, _dead_since(25))
    world.setup_entities()
    for n in (1, 2):
        hass.states.async_set(sensor(n), "unavailable")
    assert await world.setup(_conf())
    assert _state(hass, "sensor.floor_heating_mode").state == "failsafe"
    reason = _state(hass, "sensor.zone_1_floor_heating_reason")
    assert reason.state == "failsafe_waiting"
    assert reason.attributes["until"] == START.replace(hour=10).isoformat()
    assert _state(hass, "sensor.floor_heating_heat_source").state == "failsafe_waiting"
    assert [c.data["title"] for c in phone] == ["Floor heating: failsafe started"]
    alerts = _state(hass, "sensor.floor_heating_alerts").attributes["alerts"]
    assert [a["kind"] for a in alerts] == ["failsafe_started", *["sensor_fault_started"] * 2]

    await world.advance_to("10:00", keep_reporting=False)
    assert world.state(HEAT_SOURCE) == "on"
    assert world.state(valve(1)) == world.state(valve(2)) == "on"
    assert _state(hass, "sensor.floor_heating_heat_source").state == "failsafe_heating"

    world.temp(1, 22.0)
    await hass.async_block_till_done()
    assert _state(hass, "sensor.floor_heating_mode").state == "normal"
    assert phone[-1].data["title"] == "Floor heating: failsafe ended"


# ---------------------------------------------------------------- valve exercise


async def test_valve_exercise_in_ha(
    world: World, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Off season, Monday 08:00: the valves one after another, logged, not notified."""
    phone = async_mock_service(hass, "notify", "phone")
    world.setup_entities()
    assert await world.setup(_conf())
    await _call(hass, "switch", "turn_off", "switch.floor_heating_heating_season")
    await world.advance_to("08:00")
    assert world.state(valve(1)) == "on"
    assert world.state(valve(2)) == "off"
    assert world.state(HEAT_SOURCE) == "off"
    reason = _state(hass, "sensor.zone_1_floor_heating_reason")
    assert reason.state == "valve_exercise"
    assert reason.attributes["until"] == START.replace(hour=8, minute=15).isoformat()
    assert "Valve exercise: the valve of Zone 1 is open until" in caplog.text
    await world.advance_to("08:15")
    assert (world.state(valve(1)), world.state(valve(2))) == ("off", "on")
    await world.advance_to("08:30")
    assert world.state(valve(2)) == "off"
    assert "Valve exercise finished" in caplog.text
    assert phone == []


# ---------------------------------------------------------------- long run alarm


async def test_long_run_alarm_in_ha(world: World, hass: HomeAssistant) -> None:
    phone = async_mock_service(hass, "notify", "phone")
    world.setup_entities(temp=20.0)
    assert await world.setup(_conf())
    await _call(hass, "number", "set_value", "number.floor_heating_long_run_alarm", value=2)
    await world.advance_to("06:30")
    assert world.state(HEAT_SOURCE) == "on"
    await world.advance_to("08:31")
    assert [c.data["title"] for c in phone] == ["Floor heating: heat source long run"]
    alerts = _state(hass, "sensor.floor_heating_alerts").attributes["alerts"]
    assert [a["kind"] for a in alerts] == ["long_run"]

    world.temp(1, 23.0)
    world.temp(2, 23.0)
    await hass.async_block_till_done()
    assert world.state(HEAT_SOURCE) == "off"
    assert phone[-1].data["title"] == "Floor heating: heat source back to normal"
    assert _state(hass, "sensor.floor_heating_alerts").state == "0"
