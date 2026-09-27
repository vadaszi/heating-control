"""Multi-day simulations of the core against a thermal model (implementation plan, P2).

Invariants checked on every run:
- the actual HP request never changes within HpMinOnTime / HpMinOffTime;
- after warm-up, every valved zone stays within StartTemp - lower .. StopTemp + upper,
  with the bounds derived from the house model (see `bounds`); an unvalved zone gets
  heat whenever the HP runs, so only its lower bound is checked;
- no zone waits longer than WaitTime;
- at most one calling zone and at most one sync per cycle;
- `step` is idempotent (checked by the harness on every step).
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest

from custom_components.floorheat.core.config import GlobalParams, ZoneParams
from custom_components.floorheat.core.state import ZoneMode

from ..core.harness import Scenario
from .thermal import Dropout, Trace, Window, ZoneModel, duration, periods, random_windows, simulate

DAYS = 3
MINUTES = DAYS * 24 * 60
WARM_UP = 6 * 60  # minutes before the temperature bounds are checked

PARAMS = GlobalParams()
ZONE = ZoneParams()
START = ZONE.base_setpoint - ZONE.hysteresis
STOP = ZONE.base_setpoint + ZONE.hysteresis
MARGIN = 0.1  # °C


def bounds(model: ZoneModel) -> tuple[float, float]:
    """Allowed temperature range of a zone, from its model.

    - Below StartTemp: after calling, a zone stays unheated for at most
      max(WaitTime, HpMinOffTime), since both run in parallel (D-39).
    - Above StopTemp: the D-20 spread heats satisfied zones for at most HpMinOnTime.
    """
    unheated = max(ZONE.wait_time, PARAMS.hp_min_off_time) / timedelta(hours=1)
    cooling = model.loss * (START - model.outdoor)  # °C/h near StartTemp
    heating = model.heat_gain - model.loss * (STOP - model.outdoor)  # net °C/h near StopTemp
    spread = PARAMS.hp_min_on_time / timedelta(hours=1)
    return START - cooling * unheated - MARGIN, STOP + max(heating, 0.0) * spread + MARGIN


def _reference_house(outdoor: float = 5.0) -> dict[str, ZoneModel]:
    """Four valved zones with different gains and one unvalved zone that needs the
    most heat (as in the reference installation). Every zone can hold 22 °C at -10 °C."""
    gains = {"zone_1": 1.0, "zone_2": 1.2, "zone_3": 1.5, "zone_4": 0.9, "zone_5": 0.8}
    starts = {"zone_1": 21.9, "zone_2": 22.1, "zone_3": 21.7, "zone_4": 22.0, "zone_5": 21.9}
    return {z: ZoneModel(temp=starts[z], heat_gain=g, outdoor=outdoor) for z, g in gains.items()}


def check_invariants(
    sc: Scenario,
    trace: Trace,
    house: dict[str, ZoneModel],
    *,
    skip: dict[str, range] | None = None,
) -> None:
    samples = trace.samples
    skip = skip or {}

    # HP min ON / min OFF from actual transitions.
    runs = periods(samples, "hp_running")
    for index, (running, run) in enumerate(runs[:-1]):  # the last run is incomplete
        if running:
            assert duration(run) >= PARAMS.hp_min_on_time, f"short ON period at {run[0].now}"
        elif index > 0:  # the first OFF period has no known start (D-78)
            assert duration(run) >= PARAMS.hp_min_off_time, f"short OFF period at {run[0].now}"

    # Temperature bounds after warm-up.
    has_valve = {z.id: z.has_valve for z in sc.config.zones}
    limits = {zone: bounds(model) for zone, model in house.items()}
    for minute, sample in enumerate(samples[WARM_UP:], start=WARM_UP):
        for zone, temp in sample.temps.items():
            if zone in skip and minute in skip[zone]:
                continue
            low, high = limits[zone]
            assert temp >= low, f"{zone} too cold ({temp:.2f} < {low:.2f}) at {sample.now}"
            if has_valve[zone]:
                assert temp <= high, f"{zone} too warm ({temp:.2f} > {high:.2f}) at {sample.now}"

    # WAITING never lasts longer than WaitTime.
    for zone in sc.config.zone_ids:
        waiting = 0
        for sample in samples:
            waiting = waiting + 1 if sample.modes[zone] is ZoneMode.WAITING else 0
            assert waiting <= ZONE.wait_time / timedelta(minutes=1), f"{zone} waits too long"

    # One calling zone and one sync per cycle (a cycle = the request ON).
    for request, run in periods(samples, "request"):
        if not request:
            assert all(s.calling_zone is None and not s.sync_fired for s in run)
            continue
        callers = {s.calling_zone for s in run} - {None}
        assert len(callers) <= 1, f"several calling zones in one cycle at {run[0].now}"
        syncs = sum(1 for a, b in pairwise(run) if b.sync_fired and not a.sync_fired)
        assert syncs <= 1


def _cycles(trace: Trace) -> int:
    return sum(1 for running, _ in periods(trace.samples, "hp_running") if running)


def test_reference_house() -> None:
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house()
    trace = simulate(sc, house, MINUTES)
    check_invariants(sc, trace, house)
    assert _cycles(trace) >= 5  # the run exercises many cycles
    assert any(s.sync_fired for s in trace.samples)


@pytest.mark.parametrize("outdoor", [-10.0, 12.0])
def test_reference_house_cold_and_mild(outdoor: float) -> None:
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house(outdoor)
    trace = simulate(sc, house, MINUTES)
    check_invariants(sc, trace, house)


def test_all_zones_valved() -> None:
    house = _reference_house()
    house["zone_5"].heat_gain = 1.1
    sc = Scenario(5, start="00:00")
    trace = simulate(sc, house, MINUTES)
    check_invariants(sc, trace, house)


def test_window_openings_do_not_break_the_invariants() -> None:
    zones = [f"zone_{i}" for i in range(1, 6)]
    windows = random_windows(zones, MINUTES, count=40, seed=7)
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house()
    trace = simulate(sc, house, MINUTES, windows=windows)
    check_invariants(sc, trace, house)


def test_short_window_opening_does_not_start_the_hp() -> None:
    """A 15 min window dip is filtered by the 30 min WaitTime."""
    house = {z: ZoneModel(temp=22.0, loss=0.0) for z in ("zone_1", "zone_2")}
    sc = Scenario(2, start="00:00")
    trace = simulate(sc, house, 120, windows=[Window("zone_1", start=10, minutes=15)])
    assert not any(s.request for s in trace.samples)
    assert any(s.modes["zone_1"] is ZoneMode.WAITING for s in trace.samples)


def test_sensor_dropout_mid_run() -> None:
    dropout = Dropout("zone_3", start=24 * 60, minutes=3 * 60)
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house()
    trace = simulate(sc, house, MINUTES, dropouts=[dropout])
    faulty = [s for s in trace.samples if s.modes["zone_3"] is ZoneMode.SENSOR_FAULT]
    assert faulty, "the dropout must cause a sensor fault"
    first = trace.samples.index(faulty[0])
    assert first == dropout.start + 60  # > SensorFaultTimeout after the last reading
    # The faulty zone follows the house; its temperature is not controlled meanwhile.
    check_invariants(
        sc, trace, house, skip={"zone_3": range(dropout.start, dropout.start + 6 * 60)}
    )
