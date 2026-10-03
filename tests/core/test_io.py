"""Inputs/outputs/events of the step function."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.multizone_floor_heating_manager.core.config import GlobalParams, ZoneParams
from custom_components.multizone_floor_heating_manager.core.io import (
    Event,
    EventKind,
    Inputs,
    Outputs,
    OutputState,
    ZoneInput,
)

T0 = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("state", "is_on"),
    [(OutputState.ON, True), (OutputState.OFF, False), (OutputState.UNAVAILABLE, False)],
)
def test_unavailable_output_counts_as_off(state: OutputState, is_on: bool) -> None:
    """An unavailable switch counts as OFF, but stays distinguishable for the mismatch alert."""
    assert state.is_on is is_on


def test_inputs_carry_everything_step_needs() -> None:
    inputs = Inputs(
        zones={
            "living_room": ZoneInput(reading=21.8, last_reported=T0, valve=OutputState.OFF),
            "bathroom": ZoneInput(reading=None, last_reported=None, valve=None),
        },
        heat_source=OutputState.UNAVAILABLE,
        zone_params={"living_room": ZoneParams(), "bathroom": ZoneParams(base_setpoint=23.0)},
        global_params=GlobalParams(),
        heating_season=True,
        shadow_mode=True,
        time_zone=UTC,
        reconcile_tick=False,
    )
    assert inputs.zones["bathroom"].valve is None  # unvalved zone
    assert not inputs.heat_source.is_on
    assert inputs.zone_params["bathroom"].base_setpoint == 23.0


def test_outputs_and_events() -> None:
    outputs = Outputs(heat_source_on=True, valves={"living_room": True})
    assert outputs.valves == {"living_room": True}

    event = Event(kind=EventKind.SENSOR_FAULT_STARTED, message="No reading", zone_id="bathroom")
    assert event.data == {}
    assert Event(kind=EventKind.OUTPUT_MISMATCH, message="x").zone_id is None
