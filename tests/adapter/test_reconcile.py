"""Reconcile loop in live mode (docs/design.md §5.3; A25, A27; D-95, D-99, D-108)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant

from custom_components.floorheat.core.io import Event as CoreEvent
from custom_components.floorheat.core.io import EventKind

from .conftest import HEAT_SOURCE, World, make_conf, valve


async def _heating(world: World) -> None:
    """Zone 1 cold since 06:00; HP ON and zone 1 HEATING from 06:30."""
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.hass.async_block_till_done()
    assert world.mode(1) == "waiting"
    await world.advance_to("06:30")
    assert world.mode(1) == "heating"


async def test_zone_starts_the_heat_source_after_wait(world: World) -> None:
    await _heating(world)
    assert world.state(HEAT_SOURCE) == "on"
    assert world.state(valve(1)) == "on"
    assert world.state(valve(2)) == "off"
    assert world.controller.state.calling_zone == "zone_1"
    assert (HEAT_SOURCE, "on") in world.calls
    assert (valve(1), "on") in world.calls
    # the switch change was seen at once, not only at the next tick
    assert world.controller.state.hp_actual_on is True


async def test_sensor_update_runs_the_loop_at_once(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.hass.async_block_till_done()
    assert world.mode(1) == "waiting"
    assert world.reason(1) == "Waiting"


async def test_a25_manual_valve_change_is_corrected(world: World) -> None:
    await _heating(world)
    world.calls.clear()
    world.switch(valve(1), "off")  # e.g. switched in the Shelly app
    await world.hass.async_block_till_done()
    assert world.state(valve(1)) == "on"
    assert world.calls == [(valve(1), "on")]

    world.calls.clear()
    world.switch(valve(2), "on")
    await world.advance(1)
    assert world.state(valve(2)) == "off"
    assert world.calls == [(valve(2), "off")]


async def test_a27_unavailable_valve_alerts_once_and_recovers(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    await _heating(world)
    world.calls.clear()
    world.switch(valve(1), "unavailable")
    await world.advance(2)
    assert "does not follow its command" not in caplog.text
    await world.advance(1)
    assert caplog.text.count("The valve of Zone 1 does not follow its command") == 1
    await world.advance(20)
    assert caplog.text.count("The valve of Zone 1 does not follow its command") == 1
    assert world.calls == []  # nothing is sent to an unavailable switch

    world.switch(valve(1), "off")  # back, but OFF (e.g. the device rebooted)
    await world.hass.async_block_till_done()
    assert world.calls == [(valve(1), "on")]  # the first command goes out at once
    await world.advance(1)
    assert "The valve of Zone 1 follows its command again" in caplog.text


async def test_a27_ignored_command_backs_off(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await _heating(world)
    world.ignoring.add(valve(2))
    world.calls.clear()
    world.temp(2, 21.8)  # joins at once (HP running) -> valve 2 should open
    await world.hass.async_block_till_done()
    sent_at = [0]
    for minute in range(1, 60):
        before = len(world.calls)
        await world.advance(1)
        if len(world.calls) > before:
            sent_at.append(minute)
    assert all(call == (valve(2), "on") for call in world.calls)
    # first at once, then after 1, 2, 4 and 8 min, then every 15 min
    assert sent_at == [0, 1, 3, 7, 15, 30, 45]
    assert caplog.text.count("The valve of Zone 2 does not follow its command") == 1

    world.ignoring.clear()
    world.switch(valve(2), "on")
    await world.advance(1)
    assert "The valve of Zone 2 follows its command again" in caplog.text


async def test_new_desired_state_is_sent_at_once_during_backoff(world: World) -> None:
    await _heating(world)
    world.ignoring.add(valve(2))
    world.temp(2, 21.8)
    await world.hass.async_block_till_done()
    await world.advance(2)
    world.calls.clear()
    world.temp(2, 22.2)  # StopTemp: valve 2 should close again; it is still OFF
    await world.hass.async_block_till_done()
    assert world.calls == []  # already OFF: nothing to send


async def test_heat_source_back_on_after_glitch_gets_no_off(world: World) -> None:
    """A Wi-Fi glitch must never switch a working heat pump OFF (D-95, review E)."""
    await _heating(world)
    world.calls.clear()
    world.switch(HEAT_SOURCE, "unavailable")
    await world.advance(3)
    world.switch(HEAT_SOURCE, "on")
    await world.hass.async_block_till_done()
    await world.advance(3)
    assert (HEAT_SOURCE, "off") not in world.calls
    assert world.state(HEAT_SOURCE) == "on"


async def test_no_run_before_ha_has_started(world: World, hass: HomeAssistant) -> None:
    hass.set_state(CoreState.starting)
    world.setup_entities(hp="on")
    assert await world.setup(live=False)
    await world.controller.async_set_control_active(True)
    await world.advance(3)
    assert world.controller.outputs is None
    assert world.calls == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert world.controller.outputs is not None


async def test_unvalved_zone_has_no_output(world: World) -> None:
    world.setup_entities()
    assert await world.setup(make_conf(2, unvalved=(2,)))
    world.temp(2, 21.8)
    await world.advance(30)
    assert world.mode(2) == "heating"
    assert world.state(HEAT_SOURCE) == "on"
    assert {entity for entity, _ in world.calls} == {HEAT_SOURCE}


async def test_failed_command_is_logged_and_retried(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.setup_entities()
    assert await world.setup()

    world.failing.add(valve(2))
    world.switch(valve(2), "on")
    await world.hass.async_block_till_done()
    assert "Switching switch.valve_2 OFF failed: device offline" in caplog.text
    caplog.clear()
    await world.advance(1)
    assert "Switching switch.valve_2 OFF failed" in caplog.text


async def test_hanging_command_is_cancelled_on_stop(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    world.hanging.add(valve(2))
    world.switch(valve(2), "on")
    for _ in range(5):
        await asyncio.sleep(0)
    assert world.calls == [(valve(2), "off")]
    await world.controller.async_stop()
    await world.controller.async_stop()  # idempotent
    world.switch(valve(2), "on")
    await world.controller.async_reconcile(tick=True)  # stopped: does nothing


async def test_listeners_and_event_handlers(world: World) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    runs: list[None] = []
    events: list[CoreEvent] = []
    remove_listener = world.controller.async_add_listener(lambda: runs.append(None))
    remove_handler = world.controller.async_add_event_handler(events.append)
    await world.advance(1)
    assert len(runs) == 1
    await world.advance(61, keep_reporting=False)
    assert [e.kind for e in events] == [EventKind.SENSOR_FAULT_STARTED] * 2
    remove_listener()
    remove_handler()
    await world.advance(1)
    assert len(runs) == 62
