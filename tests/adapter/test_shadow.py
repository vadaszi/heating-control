"""Shadow mode."""

from __future__ import annotations

import pytest

from .conftest import HEAT_SOURCE, World, valve


async def _shadow_heating(world: World) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    world.temp(1, 21.8)
    await world.advance_to("06:30")


async def test_a21_shadow_mode_decides_but_sends_nothing(world: World) -> None:
    await _shadow_heating(world)
    controller = world.controller
    assert controller.settings.shadow_mode  # ON on first install
    assert world.mode(1) == "heating"
    outputs = controller.outputs
    assert outputs is not None
    assert outputs.heat_source_on
    assert outputs.valves == {"zone_1": True, "zone_2": False}
    # the commanded state is the feedback: the core saw the heat source go ON
    assert controller.state.hp_actual_on is True
    assert controller.state.hp_last_on_at is not None
    assert world.reason(1) == "calling_zone"
    assert world.calls == []
    assert world.state(HEAT_SOURCE) == "off"


async def test_a21_shadow_follows_its_own_decisions_to_the_end(world: World) -> None:
    await _shadow_heating(world)
    world.temp(1, 22.2)
    world.temp(2, 22.2)  # at StopTemp: the sync rule adds nobody
    await world.advance_to("07:30")  # min ON (60 min) elapsed
    outputs = world.controller.outputs
    assert outputs is not None
    assert not outputs.heat_source_on
    assert world.controller.state.hp_actual_on is False
    assert world.calls == []


async def test_a21_real_switch_states_are_ignored_in_shadow(world: World) -> None:
    world.setup_entities(hp="on")
    assert await world.setup(live=False)
    await world.advance(2)
    assert world.controller.state.hp_actual_on is False  # commanded OFF, not the real ON
    world.switch(valve(1), "unavailable")
    await world.advance(5)
    assert world.calls == []


async def test_a21_no_mismatch_alert_in_shadow(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    await _shadow_heating(world)
    world.switch(valve(1), "unavailable")
    await world.advance(10)
    assert "does not follow its command" not in caplog.text


async def test_a21_shadow_mode_on_sends_one_final_safe_set(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:30")
    assert world.state(HEAT_SOURCE) == "on"
    assert world.state(valve(1)) == "on"
    world.calls.clear()

    await world.controller.async_set_shadow_mode(True)
    await world.hass.async_block_till_done()
    assert sorted(world.calls) == [(HEAT_SOURCE, "off"), (valve(1), "off")]
    assert world.controller.pending_off == frozenset()

    world.calls.clear()
    world.switch(valve(1), "on")  # later changes by hand are left alone
    world.temp(2, 21.0)
    await world.advance(90)
    assert world.calls == []
    # shadow mode continues from the safe state it commanded
    assert world.controller.state.hp_actual_on is not None


async def test_final_off_waits_for_an_unavailable_heat_source(world: World) -> None:
    """The final OFF is delivered even if the switch was unreachable."""
    world.setup_entities()
    assert await world.setup()
    world.temp(1, 21.8)
    await world.advance_to("06:30")
    world.switch(HEAT_SOURCE, "unavailable")
    await world.hass.async_block_till_done()
    world.calls.clear()

    await world.controller.async_set_shadow_mode(True)
    await world.hass.async_block_till_done()
    assert world.calls == [(valve(1), "off")]
    assert world.controller.pending_off == {HEAT_SOURCE}

    world.switch(HEAT_SOURCE, "on")  # back, still ON
    await world.hass.async_block_till_done()
    assert (HEAT_SOURCE, "off") in world.calls
    assert world.controller.pending_off == frozenset()


async def test_final_off_is_retried_until_confirmed(world: World) -> None:
    world.setup_entities(hp="on")
    assert await world.setup()
    world.ignoring.add(HEAT_SOURCE)
    world.calls.clear()
    await world.controller.async_set_shadow_mode(True)
    await world.hass.async_block_till_done()
    await world.advance(3)
    assert world.calls.count((HEAT_SOURCE, "off")) == 3  # at once, +1 min, +3 min
    world.ignoring.clear()
    world.switch(HEAT_SOURCE, "off")
    await world.hass.async_block_till_done()
    world.calls.clear()
    world.switch(HEAT_SOURCE, "on")  # e.g. switched by hand afterwards
    await world.advance(30)
    assert world.calls == []


async def test_shadow_mode_off_sets_all_outputs(world: World) -> None:
    world.setup_entities()
    world.switch(valve(2), "on")  # e.g. switched by hand
    assert await world.setup(live=False)
    await world.advance(5)
    assert world.calls == []
    await world.controller.async_set_shadow_mode(False)
    await world.hass.async_block_till_done()
    assert world.calls == [(valve(2), "off")]


async def test_going_live_while_shadow_heats_applies_min_off(world: World) -> None:
    """Shadow had the HP ON, the real switch is OFF: that counts as a stop, so min OFF
    applies before the first real start (accepted and documented)."""
    await _shadow_heating(world)
    await world.controller.async_set_shadow_mode(False)
    await world.hass.async_block_till_done()
    assert world.calls == [(valve(1), "on")]  # HEATING, held by min OFF
    assert world.reason(1) == "held_by_minimum_off_time"
    await world.advance(59)
    assert world.state(HEAT_SOURCE) == "off"
    await world.advance(1)
    assert world.state(HEAT_SOURCE) == "on"
