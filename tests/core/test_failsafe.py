"""Failsafe case 1: HA alive, every sensor dead."""

from __future__ import annotations

from datetime import UTC, date, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.multizone_floor_heating_manager.core.alerts import active_alerts
from custom_components.multizone_floor_heating_manager.core.config import GlobalParams
from custom_components.multizone_floor_heating_manager.core.failsafe import (
    failsafe,
    newest_reading,
)
from custom_components.multizone_floor_heating_manager.core.io import (
    EventKind,
    HeatSourceStatus,
    Mode,
    OutputState,
    Reason,
)
from custom_components.multizone_floor_heating_manager.core.state import ZoneMode, ZoneState

from .harness import DAY, Scenario, at

NEXT = DAY + timedelta(days=1)
DAY_3 = DAY + timedelta(days=2)
FAULT = ZoneMode.SENSOR_FAULT


def _dead_from(
    hhmm: str, zones: int = 3, *, params: GlobalParams | None = None, **kwargs: object
) -> Scenario:
    """Every sensor reports until `hhmm` on DAY, then nothing; zone 3 has no valve."""
    sc = Scenario(zones, unvalved=(3,) if zones >= 3 else (), params=params, **kwargs)  # type: ignore[arg-type]
    sc.step()
    sc.advance_to(hhmm)
    for zone in sc.config.zone_ids:
        sc.silence(zone)
    return sc


def test_starts_24_h_after_the_newest_reading() -> None:
    """Sensors die at 06:00: sensor faults from 07:01, the failsafe from 06:01 the next
    day (> FailsafeTrigger), waiting for the 10:00 window."""
    sc = _dead_from("06:00")
    sc.advance_to("07:01")
    assert set(sc.modes().values()) == {FAULT}
    assert sc.system_mode() is Mode.NORMAL
    sc.advance_to("06:00", NEXT)
    assert sc.system_mode() is Mode.NORMAL  # exactly 24 h: not yet
    assert sc.events_of(EventKind.FAILSAFE_STARTED) == []
    sc.advance(1)
    assert sc.system_mode() is Mode.FAILSAFE
    [started] = sc.events_of(EventKind.FAILSAFE_STARTED)
    assert "more than 24 h" in started.message
    assert "10:00\u201315:00" in started.message
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.reason(1) == Reason.FAILSAFE_WAITING
    assert sc.until(1) == at("10:00", NEXT)
    assert sc.source_status == HeatSourceStatus.FAILSAFE_WAITING
    assert sc.source_until == at("10:00", NEXT)
    assert active_alerts(sc.config, sc.state)[0].kind is EventKind.FAILSAFE_STARTED


def test_heats_daily_in_the_window_with_every_valve_open() -> None:
    sc = _dead_from("06:00")
    sc.advance_to("09:59", NEXT)
    assert not sc.hp
    sc.advance(1)  # 10:00
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_2"}  # zone 3 has no valve
    assert sc.valve(3) is None
    assert {sc.reason(z) for z in (1, 2, 3)} == {Reason.FAILSAFE_HEATING}
    assert sc.until(1) == at("15:00", NEXT)
    assert sc.source_status == HeatSourceStatus.FAILSAFE_HEATING
    assert sc.source_until == at("15:00", NEXT)
    assert sc.calling_zone is None  # no zone has a reading
    sc.advance_to("15:00", NEXT)
    assert not sc.hp  # min ON (60 min) long over
    assert sc.open_valves() == set()
    assert sc.reason(1) == Reason.FAILSAFE_WAITING
    assert sc.until(1) == at("10:00", DAY_3)
    sc.advance_to("10:00", DAY_3)
    assert sc.hp
    assert len(sc.events_of(EventKind.FAILSAFE_STARTED)) == 1


def test_reached_inside_the_window_heats_for_the_rest_of_it() -> None:
    sc = _dead_from("11:00")
    sc.advance_to("11:00", NEXT)
    assert sc.system_mode() is Mode.NORMAL
    sc.advance(1)
    assert sc.system_mode() is Mode.FAILSAFE
    assert sc.hp
    assert sc.reason(1) == Reason.FAILSAFE_HEATING
    sc.advance_to("14:59", NEXT)
    assert sc.hp
    sc.advance(1)
    assert not sc.hp


def test_min_on_keeps_the_heat_source_running_after_a_late_start() -> None:
    """Reached at 14:51: the window ends at 15:00, min ON keeps it running (all valves
    open) until 15:51."""
    sc = _dead_from("14:50")
    sc.advance_to("14:51", NEXT)
    assert sc.hp
    sc.advance_to("15:00", NEXT)
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_2"}
    assert sc.reason(1) == Reason.SPREADING_HEAT
    assert sc.until(1) == at("15:51", NEXT)
    assert sc.source_status == HeatSourceStatus.SPREADING_HEAT
    sc.advance_to("15:51", NEXT)
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.reason(1) == Reason.FAILSAFE_WAITING


def test_min_off_holds_back_the_window_start() -> None:
    """The window is moved to start right after the heat source stopped: it waits for
    HpMinOffTime; the valves follow the heat source, so they stay closed meanwhile."""
    sc = _dead_from("06:00")
    sc.advance_to("15:00", NEXT)
    assert not sc.hp
    sc.global_params = GlobalParams(
        failsafe_window_start=time(15, 30), failsafe_window_end=time(18, 0)
    )
    sc.advance_to("15:30", NEXT)
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF
    assert sc.until(1) == at("16:00", NEXT)
    assert sc.source_status == HeatSourceStatus.HELD_BY_MIN_OFF
    sc.advance_to("16:00", NEXT)
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_2"}


def test_window_across_midnight() -> None:
    params = GlobalParams(failsafe_window_start=time(22, 0), failsafe_window_end=time(3, 0))
    sc = _dead_from("06:00", params=params)
    sc.advance_to("06:01", NEXT)
    assert sc.reason(1) == Reason.FAILSAFE_WAITING
    assert sc.until(1) == at("22:00", NEXT)
    sc.advance_to("22:00", NEXT)
    assert sc.hp
    assert sc.until(1) == at("03:00", DAY_3)
    sc.advance_to("03:00", DAY_3)
    assert not sc.hp
    assert sc.until(1) == at("22:00", DAY_3)


def test_first_valid_reading_ends_it_at_once() -> None:
    """In the window, zone 1 reports 21.0 °C: normal control at once; the running heat
    pump keeps zone 1 heating and it becomes the calling zone."""
    sc = _dead_from("06:00")
    sc.advance_to("11:00", NEXT)
    assert sc.hp
    sc.temp(1, 21.0)
    sc.step()
    assert sc.system_mode() is Mode.NORMAL
    [ended] = sc.events_of(EventKind.FAILSAFE_ENDED)
    assert "a sensor reports again" in ended.message
    assert sc.mode(1) is ZoneMode.HEATING
    assert sc.calling_zone == "zone_1"
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_2"}  # zone 2 faulty: follows the house
    assert sc.reason(2) == Reason.SENSOR_FAULT
    assert active_alerts(sc.config, sc.state)[0].kind is EventKind.SENSOR_FAULT_STARTED


def test_no_failsafe_while_one_sensor_still_reports() -> None:
    sc = Scenario(2)
    sc.step()
    sc.silence(2)
    sc.advance_to("12:00", NEXT)
    assert sc.mode(2) is FAULT
    assert sc.system_mode() is Mode.NORMAL
    assert sc.events_of(EventKind.FAILSAFE_STARTED) == []


def test_a_short_trigger_waits_for_the_sensor_faults() -> None:
    """FailsafeTrigger 1 h, SensorFaultTimeout 4 h: the last reading counts until the
    sensor fault, so the failsafe starts with the faults, 4 h after it."""
    params = GlobalParams(
        failsafe_trigger=timedelta(hours=1), sensor_fault_timeout=timedelta(hours=4)
    )
    sc = _dead_from("06:00", params=params)
    sc.advance_to("10:00")
    assert sc.system_mode() is Mode.NORMAL
    sc.advance(1)
    assert set(sc.modes().values()) == {FAULT}
    assert sc.system_mode() is Mode.FAILSAFE
    assert sc.hp  # inside the 10:00-15:00 window


def test_counts_from_startup_without_any_reading() -> None:
    """No reading since the first start: the trigger counts from startup."""
    sc = Scenario(2, temps=None)  # type: ignore[arg-type]
    sc.step()
    sc.advance_to("06:00", NEXT)
    assert sc.system_mode() is Mode.NORMAL
    sc.advance(1)
    assert sc.system_mode() is Mode.FAILSAFE


def test_only_in_the_heating_season() -> None:
    """Off season: no failsafe and no notification. Season ON with the sensors dead for
    longer than the trigger: it starts at once; season OFF again ends it."""
    sc = _dead_from("06:00")
    sc.set_season(False)
    sc.advance_to("11:00", NEXT)
    assert sc.system_mode() is Mode.NORMAL
    assert not sc.hp
    assert sc.events_of(EventKind.FAILSAFE_STARTED) == []
    sc.set_season(True)
    sc.step()
    assert sc.system_mode() is Mode.FAILSAFE
    assert sc.hp
    assert len(sc.events_of(EventKind.FAILSAFE_STARTED)) == 1
    sc.advance(10)
    sc.set_season(False)
    sc.step()
    assert sc.system_mode() is Mode.NORMAL
    assert not sc.hp  # season OFF overrides min ON
    assert sc.open_valves() == set()
    [ended] = sc.events_of(EventKind.FAILSAFE_ENDED)
    assert "heating season was switched off" in ended.message


def test_not_notified_again_after_a_restart() -> None:
    sc = _dead_from("06:00")
    sc.advance_to("08:00", NEXT)
    sc.restart(downtime=10)
    sc.step()
    assert sc.system_mode() is Mode.FAILSAFE
    assert len(sc.events_of(EventKind.FAILSAFE_STARTED)) == 1


def test_notified_in_shadow_mode() -> None:
    sc = _dead_from("06:00")
    sc.shadow_mode = True
    sc.advance_to("10:00", NEXT)
    assert len(sc.events_of(EventKind.FAILSAFE_STARTED)) == 1
    assert sc.hp  # the simulated decision


def test_heat_source_unavailable_in_the_window() -> None:
    sc = _dead_from("06:00")
    sc.advance_to("11:00", NEXT)
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    assert sc.reason(1) == Reason.HEAT_SOURCE_UNAVAILABLE
    assert sc.source_status == HeatSourceStatus.UNAVAILABLE
    assert sc.system_mode() is Mode.FAILSAFE


def test_failsafe_beats_holiday() -> None:
    sc = _dead_from("06:00")
    sc.holiday_without_end()
    sc.advance_to("08:00", NEXT)
    assert sc.holiday_active
    assert sc.system_mode() is Mode.FAILSAFE


# ---------------------------------------------------------------- the functions


def test_newest_reading_uses_the_wait_start_of_a_zone_without_reading() -> None:
    t1, t2 = at("06:00"), at("07:00")
    states = [ZoneState(last_valid_at=t1), ZoneState(awaiting_reading_since=t2)]
    assert newest_reading(states) == t2
    assert newest_reading([ZoneState()]) is None


def test_inactive_without_any_known_reading_time() -> None:
    assert failsafe([ZoneState()], False, True, GlobalParams(), at("12:00"), UTC) is None


def test_window_in_the_spring_dst_gap() -> None:
    """DST: a window start in the gap (02:30 on the spring change) is shifted by the
    gap length (03:30 local); a window that DST leaves empty is skipped."""
    tz = ZoneInfo("Europe/Budapest")
    dst_day = date(2026, 3, 29)
    dead = [ZoneState(last_valid_at=at("00:00", dst_day - timedelta(days=3), tz))]
    params = GlobalParams(failsafe_window_start=time(2, 30), failsafe_window_end=time(5, 0))
    before = failsafe(dead, False, True, params, at("01:00", dst_day, tz), tz)
    assert before is not None
    assert not before.in_window
    assert before.until == at("03:30", dst_day, tz)
    inside = failsafe(dead, False, True, params, at("03:30", dst_day, tz), tz)
    assert inside is not None
    assert inside.in_window

    empty = GlobalParams(failsafe_window_start=time(2, 30), failsafe_window_end=time(3, 0))
    skipped = failsafe(dead, False, True, empty, at("01:00", dst_day, tz), tz)
    assert skipped is not None
    assert skipped.until == at("02:30", dst_day + timedelta(days=1), tz)
