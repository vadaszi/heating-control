"""Persistence and restart."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from homeassistant import loader
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from pytest_homeassistant_custom_component.common import async_test_home_assistant

from custom_components.multizone_floor_heating_manager.const import (
    SAVE_DELAY,
    STORAGE_KEY,
    STORAGE_VERSION,
)

from .conftest import HEAT_SOURCE, START, World, make_conf, valve


@asynccontextmanager
async def restarted(
    world: World, downtime: int = 0, prepare: Callable[[World], None] | None = None
) -> AsyncIterator[World]:
    """Stop HA like a real shutdown, wait `downtime` min, start a new HA instance."""
    world.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await world.hass.async_block_till_done()
    world.freezer.tick(timedelta(minutes=downtime))
    async with async_test_home_assistant() as hass:
        hass.data.pop(loader.DATA_CUSTOM_COMPONENTS)
        await hass.config.async_set_time_zone("UTC")
        new = World(hass, world.freezer)
        await new.async_init()
        if prepare is not None:
            prepare(new)
        try:
            yield new
        finally:
            await hass.async_stop(force=True)


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = hass_storage[STORAGE_KEY]["data"]
    return data


async def test_a22_restart_during_wait_continues_the_wait(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:20")  # 10 min left
    assert world.mode(1) == "waiting"

    def prepare(new: World) -> None:
        new.setup_entities()
        new.temp(1, 21.8)

    async with restarted(world, downtime=3, prepare=prepare) as new:
        assert await new.setup(make_conf(), live=False)
        assert not new.controller.settings.shadow_mode  # restored
        assert new.mode(1) == "waiting"
        assert new.reason(1) == "waiting"
        await new.advance(6)
        assert new.state(HEAT_SOURCE) == "off"
        await new.advance(1)
        assert new.state(HEAT_SOURCE) == "on"


async def test_a22_restart_keeps_min_on_from_the_original_start(world: World) -> None:
    world.setup_entities()
    world.temp(2, 22.2)  # never joins
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:50")  # HP ON since 06:30
    assert world.state(HEAT_SOURCE) == "on"

    def prepare(new: World) -> None:
        new.setup_entities(hp="on")  # the heat source kept running
        new.switch(valve(1), "on")
        new.temp(1, 22.2)  # satisfied: only min ON keeps the HP running
        new.temp(2, 22.2)

    async with restarted(world, downtime=5, prepare=prepare) as new:
        assert await new.setup(make_conf(), live=False)
        state = new.controller.state
        assert state.hp_last_on_at == START.replace(minute=30)
        assert state.calling_zone == "zone_1"
        new.calls.clear()
        await new.advance_to("07:29")
        assert new.state(HEAT_SOURCE) == "on"
        await new.advance(1)
        assert new.state(HEAT_SOURCE) == "off"


async def test_settings_survive_a_restart(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    controller = world.controller
    await controller.async_set_heating_season(False)
    await controller.async_set_zone_params("zone_2", base_setpoint=20.5)
    await controller.async_set_global_params(hp_min_on_time=timedelta(minutes=90))

    async with restarted(world, prepare=lambda new: new.setup_entities()) as new:
        assert await new.setup(make_conf(), live=False)
        settings = new.controller.settings
        assert not settings.shadow_mode
        assert not settings.heating_season
        assert settings.zone_params["zone_2"].base_setpoint == 20.5
        assert settings.zone_params["zone_1"].base_setpoint == 22.0
        assert settings.global_params.hp_min_on_time == timedelta(minutes=90)


async def test_first_install_defaults(world: World) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    settings = world.controller.settings
    assert settings.heating_season
    assert settings.shadow_mode


async def test_final_off_survives_a_restart(world: World) -> None:
    world.setup_entities(hp="on")
    assert await world.setup()
    world.switch(HEAT_SOURCE, "unavailable")
    await world.hass.async_block_till_done()
    await world.controller.async_set_shadow_mode(True)
    await world.hass.async_block_till_done()
    assert world.controller.pending_off == {HEAT_SOURCE}

    def prepare(new: World) -> None:
        new.setup_entities(hp="on")

    async with restarted(world, prepare=prepare) as new:
        assert await new.setup(make_conf(), live=False)
        assert new.calls == [(HEAT_SOURCE, "off")]
        assert new.controller.pending_off == frozenset()


async def test_saves_are_delayed(world: World, hass_storage: dict[str, Any]) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    assert STORAGE_KEY not in hass_storage
    world.freezer.tick(timedelta(seconds=SAVE_DELAY))
    await world.advance(1)
    stored = _stored(hass_storage)
    assert stored["settings"]["shadow_mode"] is True
    assert stored["core"]["schema_version"] == 1
    assert stored["pending_off"] == []


def _preload(hass_storage: dict[str, Any], data: object) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": data,
    }


async def test_newer_state_is_discarded(
    world: World, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    _preload(
        hass_storage,
        {"core": {"schema_version": 99}, "settings": {"shadow_mode": False}},
    )
    world.setup_entities()
    assert await world.setup(live=False)
    assert "starting as on a first start" in caplog.text
    assert not world.controller.settings.shadow_mode  # settings are separate


async def test_stored_data_without_shadow_mode_starts_in_shadow_mode(
    world: World, hass_storage: dict[str, Any]
) -> None:
    """Only `shadow_mode` is read: an older `control_active` key is ignored, so the
    integration starts in shadow mode and switches nothing."""
    _preload(hass_storage, {"settings": {"control_active": True}})
    world.setup_entities(hp="on")
    assert await world.setup(live=False)
    world.temp(1, 21.8)
    world.temp(2, 23.0)
    await world.advance_to("06:30")
    assert world.controller.settings.shadow_mode
    assert world.state("switch.floor_heating_shadow_mode") == "on"
    assert world.mode(1) == "heating"
    assert world.calls == []
    assert world.state(HEAT_SOURCE) == "on"  # left as it was


@pytest.mark.parametrize(
    ("data", "warning"),
    [
        ("garbage", "Stored data is unusable"),
        ({"settings": "garbage"}, "Stored settings are unusable"),
        (
            {"settings": {"zones": {"zone_1": {"base_setpoint": 99}}}},
            "Stored parameters are unusable",
        ),
        ({"settings": {"shadow_mode": "yes"}}, "shadow mode are unusable"),
        (
            {"settings": {"global": {"sensor_fault_reminder": "8 o'clock", "manual_max_temp": 5}}},
            "Stored parameters are unusable",
        ),
    ],
)
async def test_unusable_stored_data_falls_back_to_defaults(
    world: World,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    data: object,
    warning: str,
) -> None:
    _preload(hass_storage, data)
    world.setup_entities()
    assert await world.setup(live=False)
    assert warning in caplog.text
    settings = world.controller.settings
    assert settings.shadow_mode
    assert settings.zone_params["zone_1"].base_setpoint == 22.0


async def test_global_holiday_temperature_becomes_every_zones_value(
    world: World, hass_storage: dict[str, Any]
) -> None:
    """A stored global HolidayTemp (older versions) is copied into every zone."""
    _preload(
        hass_storage,
        {"settings": {"global": {"holiday_temp": 16.5}, "zones": {"zone_2": {"holiday_temp": 20}}}},
    )
    world.setup_entities()
    assert await world.setup(live=False)
    zones = world.controller.settings.zone_params
    assert (zones["zone_1"].holiday_temp, zones["zone_2"].holiday_temp) == (16.5, 16.5)
    assert "holiday_temp" not in world.controller.settings.to_dict()["global"]


async def test_zone_holiday_temperature_is_kept_without_the_global_one(
    world: World, hass_storage: dict[str, Any]
) -> None:
    _preload(hass_storage, {"settings": {"zones": {"zone_2": {"holiday_temp": 20}}}})
    world.setup_entities()
    assert await world.setup(live=False)
    zones = world.controller.settings.zone_params
    assert (zones["zone_1"].holiday_temp, zones["zone_2"].holiday_temp) == (18.0, 20.0)


async def test_removed_zone_is_dropped(
    world: World, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    world.setup_entities(3)
    assert await world.setup(make_conf(3))

    async with restarted(world, prepare=lambda new: new.setup_entities(2)) as new:
        assert await new.setup(make_conf(2), live=False)
        assert set(new.controller.state.zones) == {"zone_1", "zone_2"}
    assert "zones no longer configured: zone_3" in caplog.text
