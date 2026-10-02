"""Valve exercise outside the heating season (docs/design.md §3.7, D-149)."""

from __future__ import annotations

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.multizone_floor_heating_manager.core.config import (
    CoreConfig,
    GlobalParams,
    ZoneConfig,
)
from custom_components.multizone_floor_heating_manager.core.exercise import exercise_slot
from custom_components.multizone_floor_heating_manager.core.io import OutputState, Reason

from .harness import DAY, Scenario, at

# DAY is a Monday; the default exercise is Monday 08:00, 15 min per valve.


def _off_season(start: str = "07:00", zones: int = 4, **kwargs: object) -> Scenario:
    """Zone 3 has no valve; the heating season is OFF."""
    sc = Scenario(zones, unvalved=(3,), start=start, **kwargs)  # type: ignore[arg-type]
    sc.set_season(False)
    sc.step()
    return sc


def test_valves_one_after_another_in_yaml_order() -> None:
    sc = _off_season()
    sc.advance_to("07:59")
    assert sc.open_valves() == set()
    assert sc.valve_exercise is None
    sc.advance(1)
    assert sc.open_valves() == {"zone_1"}
    assert sc.valve_exercise == "zone_1"
    assert sc.reason(1) == Reason.VALVE_EXERCISE
    assert sc.until(1) == at("08:15")
    assert sc.reason(2) == Reason.SEASON_OFF
    assert not sc.hp
    sc.advance_to("08:15")
    assert sc.open_valves() == {"zone_2"}
    sc.advance_to("08:30")
    assert sc.open_valves() == {"zone_4"}  # zone 3 has no valve: skipped
    assert sc.until(4) == at("08:45")
    sc.advance_to("08:45")
    assert sc.open_valves() == set()
    assert sc.valve_exercise is None
    assert sc.reason(4) == Reason.SEASON_OFF
    assert not sc.hp


def test_a_missed_run_is_skipped() -> None:
    """HA was down at 08:00: no catch-up after the run's end."""
    sc = _off_season("09:00")
    sc.advance_to("23:59")
    assert sc.open_valves() == set()
    sc.advance_to("08:00", DAY + timedelta(days=7))
    assert sc.open_valves() == {"zone_1"}  # next Monday


def test_restart_mid_run_continues_with_the_remaining_valves() -> None:
    sc = _off_season()
    sc.advance_to("08:10")
    sc.restart(downtime=10)
    sc.step()
    assert sc.open_valves() == {"zone_2"}


def test_season_on_ends_it() -> None:
    sc = _off_season()
    sc.advance_to("08:05")
    assert sc.open_valves() == {"zone_1"}
    sc.set_season(True)
    sc.step()
    assert sc.open_valves() == set()
    assert sc.valve_exercise is None
    assert sc.reason(1) == Reason.IDLE


def test_never_in_the_heating_season() -> None:
    sc = Scenario(2, start="07:00")
    sc.advance_to("09:00")
    assert sc.open_valves() == set()


def test_day_time_and_duration_are_settings() -> None:
    params = GlobalParams(
        valve_exercise_weekday=2,
        valve_exercise_time=time(14, 30),
        valve_exercise_duration=timedelta(minutes=5),
    )
    sc = _off_season(params=params)
    sc.advance_to("14:30", DAY + timedelta(days=2))  # Wednesday
    assert sc.open_valves() == {"zone_1"}
    assert sc.until(1) == at("14:35", DAY + timedelta(days=2))


def test_a_run_crosses_midnight() -> None:
    """Sunday 23:50: the second valve still runs on Monday at 00:05."""
    params = GlobalParams(valve_exercise_weekday=6, valve_exercise_time=time(23, 50))
    sc = _off_season("00:05", params=params)
    assert sc.open_valves() == {"zone_2"}
    assert sc.until(2) == at("00:20")


def test_a_faulty_zone_is_exercised_too() -> None:
    sc = _off_season()
    sc.silence(1)
    sc.advance_to("08:00")
    assert sc.reason(1) == Reason.VALVE_EXERCISE
    assert sc.open_valves() == {"zone_1"}


def test_a_valve_that_does_not_open_alerts_as_usual() -> None:
    """D-67: the exercise command counts like any other."""
    sc = _off_season()
    sc.set_valve_actual(1, OutputState.OFF)  # ignores commands
    sc.advance_to("08:02")
    assert not sc.state.zones["zone_1"].valve_output.alerted
    sc.advance(1)
    assert sc.state.zones["zone_1"].valve_output.alerted


def test_spring_dst_gap() -> None:
    """D-134: 02:30 on the spring change day is shifted to 03:30 local."""
    tz = ZoneInfo("Europe/Budapest")
    sunday = date(2026, 3, 29)
    params = GlobalParams(valve_exercise_weekday=6, valve_exercise_time=time(2, 30))
    sc = _off_season("03:00", day=sunday, tz=tz, params=params)
    sc.advance_to("03:29", sunday)
    assert sc.open_valves() == set()
    sc.advance(1)
    assert sc.open_valves() == {"zone_1"}


def test_no_valve_no_exercise() -> None:
    config = CoreConfig(zones=(ZoneConfig("a", "A", has_valve=False),))
    assert exercise_slot(config, GlobalParams(), at("08:05"), ZoneInfo("UTC")) is None
