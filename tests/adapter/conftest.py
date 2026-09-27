"""Fixtures for the HA adapter tests (pytest-homeassistant-custom-component).

`World` fakes the user's entities: temperature sensors set through the state machine
(which maintains `last_reported`), and switches whose `switch.turn_on/turn_off` services
are recorded and, unless told otherwise, followed. Time is frozen with `freezer`;
`World.advance` moves it minute by minute and fires the timers like HA would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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
    return {
        DOMAIN: {
            "heat_source_switch": HEAT_SOURCE,
            "zones": [zone_conf(n, valve=n not in unvalved) for n in range(1, zones + 1)],
            **extra,
        }
    }


def sensor(n: int) -> str:
    return f"sensor.zone_{n}_temperature"


def valve(n: int) -> str:
    return f"switch.valve_{n}"


class World:
    """The fake installation around the integration."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        self.hass = hass
        self.freezer = freezer
        self.calls: list[tuple[str, str]] = []  # (entity_id, "on" / "off")
        self.ignoring: set[str] = set()  # switches that ignore commands
        hass.services.async_register("switch", "turn_on", self._turn_on)
        hass.services.async_register("switch", "turn_off", self._turn_off)

    async def _turn_on(self, call: ServiceCall) -> None:
        self._command(call, "on")

    async def _turn_off(self, call: ServiceCall) -> None:
        self._command(call, "off")

    def _command(self, call: ServiceCall, state: str) -> None:
        entity_ids = call.data["entity_id"]
        for entity_id in [entity_ids] if isinstance(entity_ids, str) else entity_ids:
            self.calls.append((entity_id, state))
            if entity_id not in self.ignoring:
                self.hass.states.async_set(entity_id, state)

    # ------------------------------------------------------------ entities

    def temp(self, n: int, value: float | str, unit: str | None = "°C") -> None:
        attributes = {"device_class": "temperature"}
        if unit is not None:
            attributes["unit_of_measurement"] = unit
        self.hass.states.async_set(sensor(n), str(value), attributes)

    def switch(self, entity_id: str, state: str) -> None:
        self.hass.states.async_set(entity_id, state)

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
    return World(hass, freezer)
