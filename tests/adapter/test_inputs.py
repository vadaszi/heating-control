"""Reading HA states into core inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.multizone_floor_heating_manager.core.engine import step as real_step
from custom_components.multizone_floor_heating_manager.core.io import Inputs, OutputState
from custom_components.multizone_floor_heating_manager.inputs import SensorReader, read_switch

from .conftest import PKG, START, World

_T = datetime(2026, 1, 12, 6, 0, tzinfo=UTC)


def _state(value: str, **attributes: Any) -> State:
    return State("sensor.t", value, attributes, last_reported=_T, validate_entity_id=False)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("21.5", "°C", 21.5),
        ("71.6", "°F", 22.0),
        ("295.15", "K", 22.0),
        ("unavailable", "°C", None),
        ("unknown", "°C", None),
        ("nan", "°C", None),
        ("abc", "°C", None),
    ],
)
def test_sensor_reading(value: str, unit: str, expected: float | None) -> None:
    reading, reported = SensorReader().read("sensor.t", _state(value, unit_of_measurement=unit))
    assert reading == (None if expected is None else pytest.approx(expected))
    assert reported == _T


def test_missing_sensor() -> None:
    assert SensorReader().read("sensor.t", None) == (None, None)


def test_sensor_without_temperature_unit_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    reader = SensorReader()
    for _ in range(3):
        assert reader.read("sensor.t", _state("21.5", unit_of_measurement="%")) == (None, _T)
    assert reader.read("sensor.t", _state("21.5")) == (None, _T)
    assert caplog.text.count("sensor.t has no temperature unit") == 1


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("on", OutputState.ON),
        ("off", OutputState.OFF),
        ("unavailable", OutputState.UNAVAILABLE),
        ("unknown", OutputState.UNAVAILABLE),
        (None, OutputState.UNAVAILABLE),
    ],
)
def test_switch_state(state: str | None, expected: OutputState) -> None:
    value = None if state is None else State("switch.s", state)
    assert read_switch(value) is expected


async def test_fahrenheit_sensor_reaches_the_core_in_celsius(world: World) -> None:
    world.setup_entities()
    world.temp(1, 70.52, unit="°F")  # 21.4 °C
    assert await world.setup(live=False)
    outputs = world.controller.outputs
    assert outputs is not None
    assert outputs.zones["zone_1"].room_temp == pytest.approx(21.4)
    assert world.mode(1) == "waiting"


async def test_repeated_value_keeps_the_sensor_valid(world: World) -> None:
    """`last_reported` moves when the value repeats; silence leads to a fault."""
    world.setup_entities()
    assert await world.setup(live=False)
    await world.advance(90)  # the sensors keep reporting 22.0
    assert world.mode(1) == "idle"
    await world.advance(61, keep_reporting=False)
    assert world.mode(1) == "sensor_fault"


async def test_time_zone_follows_ha(
    world: World, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Inputs] = []

    def spy(config: Any, state: Any, inputs: Inputs, now: datetime) -> Any:
        seen.append(inputs)
        return real_step(config, state, inputs, now)

    monkeypatch.setattr(f"{PKG}.controller.step", spy)
    world.setup_entities()
    assert await world.setup(live=False)
    assert seen[-1].time_zone == ZoneInfo("UTC")
    assert seen[-1].reconcile_tick  # the first run at start is a tick

    await hass.config.async_set_time_zone("Europe/Budapest")
    await world.advance(1)
    assert seen[-1].time_zone == ZoneInfo("Europe/Budapest")
    assert seen[-1].reconcile_tick

    world.temp(1, 21.0)  # a sensor change is not a tick
    await hass.async_block_till_done()
    assert not seen[-1].reconcile_tick
    assert datetime.now(UTC) > START
