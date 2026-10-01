"""Multi-day simulations of the core against a thermal model (implementation plan, P2).

Invariants checked on every run:
- the actual HP request never changes within HpMinOnTime / HpMinOffTime, except that
  switching the heating season OFF ends an ON period at once (D-68);
- the request is never ON while the heating season is OFF;
- after warm-up, every valved zone stays within StartTemp - lower .. StopTemp + upper of
  its effective SetPoint, with the bounds derived from the house model (see `bounds`);
  an unvalved zone gets heat whenever the HP runs, so only its lower bound is checked.
  After a SetPoint change or a manual window a zone is first allowed to settle; a
  forced zone stays below ManualMaxTemp (P9);
- no zone waits longer than WaitTime;
- at most one sync per cycle; the calling zone is never forced and changes within a
  cycle only when it becomes forced (D-44, D-131);
- notifications: no output mismatch with a perfect reconcile loop, sensor fault start
  and recovery alternate per zone, no reminder outside the season (P3);
- `step` is idempotent (checked by the harness on every step).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

import pytest

from custom_components.multizone_floor_heating_manager.core.config import GlobalParams, ZoneParams
from custom_components.multizone_floor_heating_manager.core.io import EventKind, HeatSourceStatus
from custom_components.multizone_floor_heating_manager.core.schedule import check_new_schedule
from custom_components.multizone_floor_heating_manager.core.state import ZoneMode

from ..core.harness import DAY, Scenario
from .thermal import (
    Dropout,
    SeasonOff,
    Trace,
    Window,
    ZoneModel,
    duration,
    periods,
    random_windows,
    simulate,
)

DAYS = 3
MINUTES = DAYS * 24 * 60
WARM_UP = 6 * 60  # minutes before the temperature bounds are checked

PARAMS = GlobalParams()
ZONE = ZoneParams()
START = ZONE.base_setpoint - ZONE.hysteresis
STOP = ZONE.base_setpoint + ZONE.hysteresis
MARGIN = 0.1  # °C
SETTLE = timedelta(hours=24)  # longest time a zone may take to reach a new SetPoint band


def bounds(model: ZoneModel, setpoint: float = ZONE.base_setpoint) -> tuple[float, float]:
    """Allowed temperature range of a zone, from its model.

    - Below StartTemp: after calling, a zone stays unheated for at most
      max(WaitTime, HpMinOffTime), since both run in parallel (D-39).
    - Above StopTemp: the D-20 spread heats satisfied zones for at most HpMinOnTime.
    """
    start, stop = setpoint - ZONE.hysteresis, setpoint + ZONE.hysteresis
    unheated = max(ZONE.wait_time, PARAMS.hp_min_off_time) / timedelta(hours=1)
    cooling = model.loss * (start - model.outdoor)  # °C/h near StartTemp
    heating = model.heat_gain - model.loss * (stop - model.outdoor)  # net °C/h near StopTemp
    spread = PARAMS.hp_min_on_time / timedelta(hours=1)
    return start - cooling * unheated - MARGIN, stop + max(heating, 0.0) * spread + MARGIN


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
    skip_all: Sequence[range] = (),
) -> None:
    samples = trace.samples
    skip = skip or {}

    # HP min ON / min OFF from actual transitions; season OFF ends an ON period at once.
    runs = periods(samples, "hp_running")
    for index, (running, run) in enumerate(runs[:-1]):  # the last run is incomplete
        if running:
            season_off = not runs[index + 1][1][0].season
            short = duration(run) < PARAMS.hp_min_on_time
            assert season_off or not short, f"short ON period at {run[0].now}"
        elif index > 0:  # the first OFF period has no known start (D-78)
            assert duration(run) >= PARAMS.hp_min_off_time, f"short OFF period at {run[0].now}"

    # Nothing heats outside the season (D-24, D-68).
    for sample in samples:
        if not sample.season:
            assert not sample.request, f"request ON at {sample.now}"
            assert not sample.hp_running, f"heat pump running at {sample.now}"

    # The heat source sensor agrees with the request (D-141; the switch is always there).
    requesting = {HeatSourceStatus.HEATING, HeatSourceStatus.SPREADING_HEAT}
    for sample in samples:
        if not sample.season:
            assert sample.source_status == HeatSourceStatus.SEASON_OFF, sample.now
        else:
            assert sample.request == (sample.source_status in requesting), sample.now

    # Temperature bounds after warm-up, against the effective SetPoint (P9).
    has_valve = {z.id: z.has_valve for z in sc.config.zones}
    settling: dict[str, datetime | None] = dict.fromkeys(house)
    for minute, sample in enumerate(samples):
        previous = samples[minute - 1] if minute else sample
        for zone, temp in sample.temps.items():
            forced = sample.modes[zone] is ZoneMode.FORCED
            if forced:
                limit = PARAMS.manual_max_temp + MARGIN
                assert temp <= limit, f"{zone} above the cap ({temp:.2f}) at {sample.now}"
            setpoint = sample.setpoints[zone]
            changed = minute == 0 or setpoint != previous.setpoints[zone]
            if changed or (previous.modes[zone] is ZoneMode.FORCED and not forced):
                settling[zone] = settling[zone] or sample.now  # reaches the new band first
            since = settling[zone]
            if since is not None:
                low_ok = temp >= setpoint - ZONE.hysteresis
                if low_ok and (temp <= setpoint + ZONE.hysteresis or not has_valve[zone]):
                    settling[zone] = None
                else:
                    assert sample.now - since < SETTLE, f"{zone} does not settle ({since})"
            if (
                minute < WARM_UP
                or forced
                or settling[zone] is not None
                or (zone in skip and minute in skip[zone])
                or any(minute in r for r in skip_all)
            ):
                continue
            low, high = bounds(house[zone], setpoint)
            assert temp >= low, f"{zone} too cold ({temp:.2f} < {low:.2f}) at {sample.now}"
            if has_valve[zone]:
                assert temp <= high, f"{zone} too warm ({temp:.2f} > {high:.2f}) at {sample.now}"

    # WAITING never lasts longer than WaitTime.
    for zone in sc.config.zone_ids:
        waiting = 0
        for sample in samples:
            waiting = waiting + 1 if sample.modes[zone] is ZoneMode.WAITING else 0
            assert waiting <= ZONE.wait_time / timedelta(minutes=1), f"{zone} waits too long"

    # One calling zone and one sync per cycle (a cycle = the request ON). The calling
    # zone is never forced; it changes only when it becomes forced (D-44, D-131).
    for request, run in periods(samples, "request"):
        if not request:
            assert all(s.calling_zone is None and not s.sync_fired for s in run)
            continue
        for a, b in pairwise(run):
            caller = b.calling_zone
            assert caller is None or b.modes[caller] is not ZoneMode.FORCED, b.now
            if a.calling_zone is not None and caller != a.calling_zone:
                assert b.modes[a.calling_zone] is ZoneMode.FORCED, f"calling zone changed {b.now}"
        syncs = sum(1 for a, b in pairwise(run) if b.sync_fired and not a.sync_fired)
        assert syncs <= 1

    # Notifications.
    events = [(sample, event) for sample in samples for event in sample.events]
    kinds = {event.kind for _, event in events}
    assert EventKind.OUTPUT_MISMATCH not in kinds, "perfect reconcile: no mismatch"
    for sample, event in events:
        if event.kind is EventKind.SENSOR_FAULT_REMINDER:
            assert sample.season, f"reminder outside the season at {sample.now}"
    for zone in sc.config.zone_ids:
        faults = [
            event.kind
            for _, event in events
            if event.zone_id == zone
            and event.kind in (EventKind.SENSOR_FAULT_STARTED, EventKind.SENSOR_FAULT_RECOVERED)
        ]
        assert faults == [EventKind.SENSOR_FAULT_STARTED, EventKind.SENSOR_FAULT_RECOVERED] * (
            len(faults) // 2
        ) + [EventKind.SENSOR_FAULT_STARTED] * (len(faults) % 2), f"{zone}: {faults}"


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
    events = [e for s in trace.samples for e in s.events]
    assert [(e.kind, e.zone_id) for e in events] == [
        (EventKind.SENSOR_FAULT_STARTED, "zone_3"),
        (EventKind.SENSOR_FAULT_RECOVERED, "zone_3"),
    ]  # 01:00 to 03:00 on day 2: no reminder for a fault of the same day
    # The faulty zone follows the house; its temperature is not controlled meanwhile.
    check_invariants(
        sc, trace, house, skip={"zone_3": range(dropout.start, dropout.start + 6 * 60)}
    )


def test_long_dropout_is_reminded_once() -> None:
    """A fault from 21:00 on day 1 to 10:00 on day 2 gets one reminder at 08:00."""
    dropout = Dropout("zone_2", start=20 * 60, minutes=14 * 60)
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house()
    trace = simulate(sc, house, 2 * 24 * 60, dropouts=[dropout])
    reminders = [
        (s.now, e)
        for s in trace.samples
        for e in s.events
        if e.kind is EventKind.SENSOR_FAULT_REMINDER
    ]
    assert [(now.hour, now.minute, e.data) for now, e in reminders] == [
        (8, 0, {"zone_ids": "zone_2"})
    ]
    check_invariants(
        sc, trace, house, skip={"zone_2": range(dropout.start, dropout.start + 18 * 60)}
    )


RECOVERY = 8 * 60  # minutes after the season is back ON before the bounds apply again


def test_season_changes() -> None:
    """Season OFF blocks, several of them in the middle of a cycle, keep the invariants.
    A sensor faulty since day 1 is not reminded during the OFF morning of day 2; the
    reminder is caught up when the season is switched ON again (D-98)."""
    offs = [
        SeasonOff(start=10 * 60 + 7, minutes=6 * 60),
        SeasonOff(start=27 * 60 + 13, minutes=12 * 60),  # day 2, 03:13 to 15:13
        SeasonOff(start=50 * 60 + 29, minutes=3 * 60),
        SeasonOff(start=60 * 60 + 41, minutes=20),
    ]
    dropout = Dropout("zone_4", start=20 * 60, minutes=22 * 60)  # faulty day 1 21:00 to day 2 18:00
    sc = Scenario(5, unvalved=[5], start="00:00")
    house = _reference_house()
    trace = simulate(sc, house, MINUTES, dropouts=[dropout], season_off=offs)
    samples = trace.samples

    mid_cycle = [off for off in offs if samples[off.start - 1].hp_running]
    assert len(mid_cycle) >= 2, "the run must switch the season OFF during a cycle"
    for off in mid_cycle:
        assert not samples[off.start].hp_running  # OFF at once, even within min ON
        assert not any(samples[off.start].modes[z] is ZoneMode.HEATING for z in sc.config.zone_ids)
    assert _cycles(trace) >= 5
    reminders = [
        (s.now, e) for s in samples for e in s.events if e.kind is EventKind.SENSOR_FAULT_REMINDER
    ]
    assert [(now.day, now.hour, now.minute, e.data) for now, e in reminders] == [
        (13, 15, 13, {"zone_ids": "zone_4"})  # caught up on day 2 when the season is ON
    ]

    check_invariants(
        sc,
        trace,
        house,
        skip={"zone_4": range(dropout.start, dropout.start + dropout.minutes + 6 * 60)},
        skip_all=[range(off.start, off.start + off.minutes + RECOVERY) for off in offs],
    )


def test_week_with_schedules_and_holiday() -> None:
    """P9: a week of recurring and one-shot auto and manual schedules and a holiday keeps
    the invariants; the schedules are checked as the adapter would before storing them."""
    sc = Scenario(5, unvalved=[5], start="00:00")
    daily = range(7)
    sc.add_auto([1], "13:00", "17:00", 23.0, weekdays=daily)
    sc.add_manual([2], "04:00", "05:30", weekdays=daily)
    sc.add_manual([3], "10:00", "16:00", on=DAY + timedelta(days=2))  # reaches the cap
    sc.add_auto([4], "18:00", "23:00", 23.0, on=DAY + timedelta(days=3))
    sc.add_auto(None, "08:00", "12:00", 21.0, weekdays=[5])  # Saturday, all zones
    sc.add_manual([1, 4], "22:00", "02:00", weekdays=[6])  # Sunday night
    for index, schedule in enumerate(sc.schedules):
        others = sc.schedules[:index]
        assert check_new_schedule(schedule, others, sc.config, sc.now, sc.tz) == []
    sc.holiday("15:00", DAY + timedelta(days=1))  # Monday 00:00 to Tuesday 15:00
    house = _reference_house()
    holiday_end = 24 * 60 + 15 * 60
    trace = simulate(sc, house, 7 * 24 * 60)
    samples = trace.samples

    assert all(s.setpoints["zone_2"] == 18.0 for s in samples[:holiday_end])
    assert all(s.modes["zone_2"] is not ZoneMode.FORCED for s in samples[:holiday_end])
    assert samples[holiday_end].setpoints["zone_2"] == 22.0
    forced = {z for s in samples for z, m in s.modes.items() if m is ZoneMode.FORCED}
    assert forced == {"zone_1", "zone_2", "zone_3", "zone_4"}
    zone_3_forced = [s.temps["zone_3"] for s in samples if s.modes["zone_3"] is ZoneMode.FORCED]
    assert max(zone_3_forced) >= PARAMS.manual_max_temp - 0.01  # capped (readings: 0.01 °C)
    assert any(s.setpoints["zone_4"] == 23.0 for s in samples)
    assert any(s.setpoints["zone_5"] == 21.0 for s in samples)  # Saturday, all zones
    assert [s.id for s in sc.schedules] == ["s1", "s2", "s5", "s6"]  # one-shots deleted
    assert _cycles(trace) >= 10
    check_invariants(sc, trace, house)
