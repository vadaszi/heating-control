"""Notification events of the core: sensor fault start / reminder / recovery (§3.6,
D-75, D-98) and the output mismatch alert (§3.9, D-67, D-99)."""

from __future__ import annotations

from datetime import UTC, date, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.floorheat.core.config import GlobalParams
from custom_components.floorheat.core.engine import step
from custom_components.floorheat.core.io import EventKind
from custom_components.floorheat.core.state import CoreState, ZoneMode, ZoneState

from .harness import DAY, Scenario, at

STARTED = EventKind.SENSOR_FAULT_STARTED
REMINDER = EventKind.SENSOR_FAULT_REMINDER
RECOVERED = EventKind.SENSOR_FAULT_RECOVERED
NEXT_DAY = DAY + timedelta(days=1)
BERLIN = ZoneInfo("Europe/Berlin")


def _faulty_from_0701(zones: int = 2, **kwargs: object) -> Scenario:
    """Zone 1 never reports: SENSOR_FAULT at 07:01 on DAY (D-93)."""
    sc = Scenario(zones, temps={1: None}, **kwargs)  # type: ignore[arg-type]
    sc.step()
    sc.advance_to("07:01")
    assert sc.mode(1) is ZoneMode.SENSOR_FAULT
    return sc


# ---------------------------------------------------------------- start and recovery


def test_fault_start_and_recovery_are_notified_once() -> None:
    sc = _faulty_from_0701()
    [started] = sc.events_of(STARTED)
    assert started.zone_id == "zone_1"
    assert started.message == (
        "Sensor fault in Zone 1: no valid reading for more than 60 min. "
        "The zone follows the heat pump and creates no demand."
    )
    assert started.data == {"last_valid_at": None}
    sc.advance_to("07:30")
    assert len(sc.events_of(STARTED)) == 1

    sc.temp(1, 21.9)
    sc.step()
    [recovered] = sc.events_of(RECOVERED)
    assert recovered.zone_id == "zone_1"
    assert (
        recovered.message == "Sensor in Zone 1 reports again; the zone is back to normal control."
    )
    sc.advance(10)
    assert len(sc.events_of(RECOVERED)) == 1


def test_started_carries_the_last_valid_reading_time() -> None:
    sc = Scenario(2)
    sc.step()
    sc.silence(1)
    sc.advance_to("07:01")
    [started] = sc.events_of(STARTED)
    assert started.data == {"last_valid_at": "2026-01-12T06:00:00+00:00"}


def test_stored_fault_is_not_notified_again_after_a_restart() -> None:
    sc = _faulty_from_0701()
    sc.restart(downtime=10)
    sc.step()
    assert len(sc.events_of(STARTED)) == 1
    sc.temp(1, 21.9)  # recovery after the restart is notified
    sc.restart(downtime=1)
    sc.step()
    assert len(sc.events_of(RECOVERED)) == 1


def test_fault_that_began_while_ha_was_down_is_notified() -> None:
    sc = Scenario(2)
    sc.step()
    sc.silence(1)
    sc.restart(downtime=90)
    sc.step()
    assert [e.zone_id for e in sc.events_of(STARTED)] == ["zone_1"]


def test_fault_events_in_shadow_mode() -> None:
    """Only the mismatch alert is inactive in shadow mode (D-67, D-98)."""
    sc = Scenario(2, temps={1: None})
    sc.control_active = False
    sc.step()
    sc.advance_to("07:01")
    assert len(sc.events_of(STARTED)) == 1


# ---------------------------------------------------------------- daily reminder


def test_reminder_next_day_at_reminder_time_once() -> None:
    sc = _faulty_from_0701()
    sc.advance_to("23:59")
    assert sc.events_of(REMINDER) == []  # the fault started today (D-98)
    sc.advance_to("07:59", NEXT_DAY)
    assert sc.events_of(REMINDER) == []
    sc.advance_to("08:00", NEXT_DAY)
    [reminder] = sc.events_of(REMINDER)
    assert reminder.zone_id is None
    assert reminder.message == "Sensor fault still active in: Zone 1."
    assert reminder.data == {"zone_ids": "zone_1"}
    assert sc.state.last_fault_reminder_on == NEXT_DAY
    sc.advance_to("23:59", NEXT_DAY)
    assert len(sc.events_of(REMINDER)) == 1
    sc.advance_to("08:00", NEXT_DAY + timedelta(days=1))
    assert len(sc.events_of(REMINDER)) == 2


def test_fault_just_before_reminder_time_waits_for_the_next_day() -> None:
    sc = Scenario(2, start="06:00")
    sc.step()
    sc.advance_to("06:58")
    sc.silence(1)  # last valid reading 06:58: fault at 07:59
    sc.advance_to("07:59")
    assert sc.mode(1) is ZoneMode.SENSOR_FAULT
    sc.advance_to("12:00")
    assert sc.events_of(REMINDER) == []
    sc.advance_to("08:00", NEXT_DAY)
    assert len(sc.events_of(REMINDER)) == 1


def test_one_reminder_lists_every_zone_faulty_since_an_earlier_day() -> None:
    sc = Scenario(3, temps={1: None, 3: None})
    sc.step()
    sc.advance_to("06:00", NEXT_DAY)
    sc.silence(2)  # a fault that starts today (07:01) is not in today's reminder
    sc.advance_to("08:00", NEXT_DAY)
    assert sc.mode(2) is ZoneMode.SENSOR_FAULT
    [reminder] = sc.events_of(REMINDER)
    assert reminder.message == "Sensor fault still active in: Zone 1, Zone 3."
    assert reminder.data == {"zone_ids": "zone_1, zone_3"}


def test_no_reminder_after_recovery() -> None:
    sc = _faulty_from_0701()
    sc.advance_to("07:00", NEXT_DAY)
    sc.temp(1, 21.9)
    sc.advance_to("09:00", NEXT_DAY)
    assert sc.events_of(REMINDER) == []


def test_stored_fault_without_start_time_counts_as_an_earlier_day() -> None:
    """Defensive: a stored fault without `fault_since` is reminded (D-98)."""
    stale = ZoneState(mode=ZoneMode.SENSOR_FAULT, last_valid_value=21.0, last_valid_at=at("04:00"))
    state = CoreState(zones={"zone_1": stale, "zone_2": ZoneState()})
    sc = Scenario(2, start="08:00", state=state)
    sc.silence(1)
    sc.step()
    assert sc.events_of(STARTED) == []
    assert len(sc.events_of(REMINDER)) == 1


def test_restart_after_reminder_time_catches_up_once() -> None:
    sc = _faulty_from_0701()
    sc.advance_to("07:00", NEXT_DAY)
    sc.restart(downtime=180)  # HA was down at 08:00
    sc.step()
    assert len(sc.events_of(REMINDER)) == 1  # sent late, at 10:00
    sc.restart(downtime=5)
    sc.advance(60)
    assert len(sc.events_of(REMINDER)) == 1  # persisted: not again the same day
    assert len(sc.events_of(STARTED)) == 1


def test_season_off_at_reminder_time_then_on_catches_up() -> None:
    sc = _faulty_from_0701()
    sc.advance_to("07:00", NEXT_DAY)
    sc.set_season(False)
    sc.advance_to("10:00", NEXT_DAY)
    assert sc.events_of(REMINDER) == []  # no reminder outside the season (D-75)
    sc.set_season(True)
    sc.step()
    assert len(sc.events_of(REMINDER)) == 1


# ---------------------------------------------------------------- time zone and DST (D-96)


def test_reminder_uses_local_time() -> None:
    sc = _faulty_from_0701(tz=BERLIN)  # January: UTC+1
    sc.advance_to("08:00", NEXT_DAY)
    [reminder] = sc.events_of(REMINDER)
    assert sc.now.hour == 7  # UTC
    assert reminder.kind is REMINDER


def test_reminder_on_the_spring_dst_day() -> None:
    day = date(2026, 3, 29)  # 02:00 -> 03:00 in Europe/Berlin
    sc = _faulty_from_0701(tz=BERLIN, day=day - timedelta(days=1))
    sc.advance_to("07:59", day)
    assert sc.events_of(REMINDER) == []
    sc.advance_to("08:00", day)
    assert len(sc.events_of(REMINDER)) == 1
    assert sc.now.hour == 6  # UTC+2 since the change


def test_reminder_on_the_autumn_dst_day() -> None:
    day = date(2026, 10, 25)  # 03:00 -> 02:00 in Europe/Berlin
    sc = _faulty_from_0701(tz=BERLIN, day=day - timedelta(days=1))
    sc.advance_to("07:59", day)
    assert sc.events_of(REMINDER) == []
    sc.advance_to("08:00", day)
    assert len(sc.events_of(REMINDER)) == 1
    assert sc.now.hour == 7  # UTC+1 since the change


def test_reminder_time_inside_the_spring_gap_fires_after_the_gap() -> None:
    day = date(2026, 3, 29)
    params = GlobalParams(sensor_fault_reminder=time(2, 30))
    sc = _faulty_from_0701(tz=BERLIN, day=day - timedelta(days=1), params=params)
    sc.advance_to("01:59", day)
    sc.advance(1)  # 02:00 CET does not exist: now 03:00 CEST
    assert sc.local_now().hour == 3
    sc.advance_to("03:29", day)
    assert sc.events_of(REMINDER) == []
    sc.advance(1)
    assert len(sc.events_of(REMINDER)) == 1


def test_reminder_time_inside_the_repeated_autumn_hour_fires_once() -> None:
    day = date(2026, 10, 25)
    params = GlobalParams(sensor_fault_reminder=time(2, 30))
    sc = _faulty_from_0701(tz=BERLIN, day=day - timedelta(days=1), params=params)
    sc.advance_to("02:29", day)  # first 02:29 (CEST)
    assert sc.events_of(REMINDER) == []
    sc.advance(1)
    assert len(sc.events_of(REMINDER)) == 1
    sc.advance(120)  # through the repeated hour
    assert len(sc.events_of(REMINDER)) == 1


def test_now_in_any_time_zone_gives_the_same_result() -> None:
    sc = _faulty_from_0701(tz=BERLIN)
    sc.advance_to("07:59", NEXT_DAY)
    sc.now += timedelta(minutes=1)
    inputs = sc.inputs()
    in_utc = step(sc.config, sc.state, inputs, sc.now.astimezone(UTC))
    in_local = step(sc.config, sc.state, inputs, sc.now.astimezone(BERLIN))
    assert in_utc == in_local
    assert [e.kind for e in in_utc[2]] == [REMINDER]
