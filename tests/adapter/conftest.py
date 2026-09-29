"""Fixtures for the HA adapter tests (pytest-homeassistant-custom-component).

`World` fakes the user's entities: temperature sensors set through the state machine
(which maintains `last_reported`), and real switch entities on a `test` platform whose
commands are recorded and, unless told otherwise, followed (like a Template switch
stand-in, D-113). Time is frozen with `freezer`;
`World.advance` moves it minute by minute and fires the timers like HA would.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockPlatform,
    async_fire_time_changed,
    mock_platform,
)

from custom_components.floorheat.const import DATA_CONTROLLER, DOMAIN
from custom_components.floorheat.controller import FloorheatController

START = datetime(2026, 1, 12, 6, 0, tzinfo=UTC)  # a Monday in the heating season

HEAT_SOURCE = "switch.heat_source"


def zone_conf(n: int, *, valve: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"zone_{n}",
        "name": f"Zone {n}",
        "sensor": f"sensor.zone_{n}_temperature",
        "valve": f"switch.valve_{n}" if valve else "none",
        **extra,
    }


def make_conf(zones: int = 2, *, unvalved: tuple[int, ...] = (), **extra: Any) -> dict[str, Any]:
    """Without `shellys` / `no_watchdog`, every switch is listed in `no_watchdog` (D-118)."""
    conf: dict[str, Any] = {
        "heat_source_switch": HEAT_SOURCE,
        "zones": [zone_conf(n, valve=n not in unvalved) for n in range(1, zones + 1)],
        **extra,
    }
    if "shellys" not in extra and "no_watchdog" not in extra:
        no_watchdog_for_all(conf)
    return {DOMAIN: conf}


def no_watchdog_for_all(conf: dict[str, Any]) -> None:
    """List every mapped switch of a floorheat config in `no_watchdog`."""
    valves = [
        zone.get("valve")
        for zone in conf.get("zones", [])
        if isinstance(zone, dict) and str(zone.get("valve")).startswith("switch.")
    ]
    conf["no_watchdog"] = list(dict.fromkeys([conf["heat_source_switch"], *valves]))


def sensor(n: int) -> str:
    return f"sensor.zone_{n}_temperature"


def valve(n: int) -> str:
    return f"switch.valve_{n}"


class FakeSwitch(SwitchEntity):
    """A user's switch: follows commands (like a Template switch stand-in) unless told
    to ignore, fail or hang."""

    _attr_should_poll = False

    def __init__(self, world: World, entity_id: str, state: str) -> None:
        self.world = world
        self.entity_id = entity_id
        self._attr_name = entity_id.split(".")[1]
        self.set(state, write=False)

    def set(self, state: str, *, write: bool = True) -> None:
        self._attr_available = state != "unavailable"
        self._attr_is_on = {"on": True, "off": False}.get(state)
        if write:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.world.command(self, "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.world.command(self, "off")


class World:
    """The fake installation around the integration."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        self.hass = hass
        self.freezer = freezer
        self.calls: list[tuple[str, str]] = []  # (entity_id, "on" / "off")
        self.ignoring: set[str] = set()  # switches that ignore commands
        self.failing: set[str] = set()  # switches whose commands raise
        self.hanging: set[str] = set()  # switches whose commands never return
        self.switches: dict[str, FakeSwitch] = {}
        self._add_entities: AddEntitiesCallback | None = None

    async def async_init(self) -> None:
        """Set up HA's switch component with a `test` platform for the user's switches."""

        async def setup_platform(
            hass: HomeAssistant,
            config: Any,
            add_entities: AddEntitiesCallback,
            discovery_info: Any = None,
        ) -> None:
            self._add_entities = add_entities

        mock_platform(self.hass, "test.switch", MockPlatform(async_setup_platform=setup_platform))
        assert await async_setup_component(self.hass, "switch", {"switch": {"platform": "test"}})
        await self.hass.async_block_till_done()

    async def command(self, switch: FakeSwitch, state: str) -> None:
        self.calls.append((switch.entity_id, state))
        if switch.entity_id in self.failing:
            raise HomeAssistantError("device offline")
        if switch.entity_id in self.hanging:
            await asyncio.Event().wait()
        if switch.entity_id not in self.ignoring:
            switch.set(state)

    # ------------------------------------------------------------ entities

    def temp(self, n: int, value: float | str, unit: str | None = "°C") -> None:
        attributes = {"device_class": "temperature"}
        if unit is not None:
            attributes["unit_of_measurement"] = unit
        self.hass.states.async_set(sensor(n), str(value), attributes)

    def switch(self, entity_id: str, state: str) -> None:
        """Set a switch's state, e.g. by hand or when it goes unavailable."""
        if entity_id in self.switches:
            self.switches[entity_id].set(state)
            return
        assert self._add_entities is not None
        switch = self.switches[entity_id] = FakeSwitch(self, entity_id, state)
        self._add_entities([switch])

    def setup_entities(self, zones: int = 2, temp: float = 22.0, *, hp: str = "off") -> None:
        for n in range(1, zones + 1):
            self.temp(n, temp)
            self.switch(valve(n), "off")
        self.switch(HEAT_SOURCE, hp)

    def state(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        return None if state is None else state.state

    # ------------------------------------------------------------ integration

    async def setup(self, conf: dict[str, Any] | None = None, *, live: bool = True) -> bool:
        await self.hass.async_block_till_done()  # switches added before are in place
        ok = await async_setup_component(self.hass, DOMAIN, conf or make_conf())
        await self.hass.async_block_till_done()
        if ok and live:
            await self.controller.async_set_control_active(True)
            await self.hass.async_block_till_done()
        return ok

    @property
    def controller(self) -> FloorheatController:
        return self.hass.data[DATA_CONTROLLER]

    def mode(self, n: int) -> str:
        return self.controller.state.zones[f"zone_{n}"].mode.value

    def reason(self, n: int) -> str:
        outputs = self.controller.outputs
        assert outputs is not None
        return outputs.zones[f"zone_{n}"].reason

    # ------------------------------------------------------------ time

    async def advance(self, minutes: int = 1, *, keep_reporting: bool = True) -> None:
        """Advance time minute by minute; sensors report their value again each minute."""
        for _ in range(minutes):
            self.freezer.tick(timedelta(minutes=1))
            if keep_reporting:
                for state in self.hass.states.async_all("sensor"):
                    self.hass.states.async_set(state.entity_id, state.state, state.attributes)
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()

    async def advance_to(self, hhmm: str, **kwargs: Any) -> None:
        hour, minute = map(int, hhmm.split(":"))
        target = START.replace(hour=hour, minute=minute)
        now = datetime.now(UTC)
        assert target >= now
        await self.advance(int((target - now) / timedelta(minutes=1)), **kwargs)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load `custom_components/floorheat` in every adapter test."""


@pytest.fixture
async def world(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> World:
    freezer.move_to(START)
    await hass.config.async_set_time_zone("UTC")
    world = World(hass, freezer)
    await world.async_init()
    return world
