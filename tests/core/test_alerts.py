"""Notification events of the core: sensor fault start / reminder / recovery and the output
mismatch alert."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.multizone_floor_heating_manager.core.alerts import active_alerts
from custom_components.multizone_floor_heating_manager.core.config import (
    CoreConfig,
    GlobalParams,
    ZoneParams,
)
from custom_components.multizone_floor_heating_manager.core.engine import step
from custom_components.multizone_floor_heating_manager.core.io import EventKind, OutputState
from custom_components.multizone_floor_heating_manager.core.state import (
    CoreState,
    OutputTracking,
    ZoneMode,
    ZoneState,
)

from .harness import DAY, Scenario, at, make_config

STARTED = EventKind.SENSOR_FAULT_STARTED
REMINDER = EventKind.SENSOR_FAULT_REMINDER
RECOVERED = EventKind.SENSOR_FAULT_RECOVERED
NEXT_DAY = DAY + timedelta(days=1)
BERLIN = ZoneInfo("Europe/Berlin")


def _faulty_from_0701(zones: int = 2, **kwargs: object) -> Scenario:
    """Zone 1 never reports: SENSOR_FAULT at 07:01 on DAY."""
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
        "The zone follows the heat source and creates no demand."
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
    """Only the mismatch alert is inactive in shadow mode."""
    sc = Scenario(2, temps={1: None})
    sc.shadow_mode = True
    sc.step()
    sc.advance_to("07:01")
    assert len(sc.events_of(STARTED)) == 1


# ---------------------------------------------------------------- daily reminder


def test_reminder_next_day_at_reminder_time_once() -> None:
    sc = _faulty_from_0701()
    sc.advance_to("23:59")
    assert sc.events_of(REMINDER) == []  # the fault started today
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
    """Defensive: a stored fault without `fault_since` is reminded."""
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
    assert sc.events_of(REMINDER) == []  # no reminder outside the season
    sc.set_season(True)
    sc.step()
    assert len(sc.events_of(REMINDER)) == 1


# ---------------------------------------------------------------- time zone and DST


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


# ---------------------------------------------------------------- output mismatch

MISMATCH = EventKind.OUTPUT_MISMATCH
MISMATCH_RECOVERED = EventKind.OUTPUT_MISMATCH_RECOVERED


def _config(zones: int = 2, alert: int = 3) -> CoreConfig:
    return CoreConfig(zones=make_config(zones).zones, output_mismatch_alert=alert)


def test_first_tick_after_a_new_command_is_not_counted() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.set_valve_actual(1, OutputState.OFF)  # ignores the command
    sc.step()
    assert sc.valve(1) is True
    assert sc.state.zones["zone_1"].valve_output == OutputTracking(0, False, True)
    sc.advance(2)
    assert sc.state.zones["zone_1"].valve_output.mismatch_count == 2
    assert sc.events_of(MISMATCH) == []
    sc.advance(1)  # three full intervals after the command
    [alert] = sc.events_of(MISMATCH)
    assert alert.zone_id == "zone_1"
    assert alert.message == (
        "The valve of Zone 1 does not follow its command: it should be ON but is OFF "
        "(3 reconcile intervals)."
    )
    assert alert.data == {"output": "valve", "desired": True, "actual": "off"}


def test_normal_operation_never_alerts_even_with_an_alert_after_one_interval() -> None:
    """Outputs follow within one interval: a new command is never a mismatch."""
    sc = Scenario(_config(3, alert=1), temps={1: 21.8, 2: 22.2, 3: 22.2})
    sc.step()
    sc.advance_to("06:30")
    assert sc.hp
    sc.temp(1, 22.2)
    sc.advance_to("08:00")
    assert not sc.hp
    assert sc.events_of(MISMATCH) == []


def test_alert_after_one_interval() -> None:
    sc = Scenario(_config(2, alert=1))
    sc.step()
    sc.set_valve_actual(2, OutputState.ON)
    sc.advance(1)
    assert len(sc.events_of(MISMATCH)) == 1


def test_only_reconcile_ticks_count() -> None:
    sc = Scenario(2)
    sc.step()
    sc.set_valve_actual(1, OutputState.UNAVAILABLE)
    sc.now += timedelta(minutes=1)
    inputs = dataclasses.replace(sc.inputs(), reconcile_tick=False)  # a sensor update
    _, state, events = step(sc.config, sc.state, inputs, sc.now)
    assert events == []
    assert state.zones["zone_1"].valve_output == sc.state.zones["zone_1"].valve_output
    assert state.reconcile_tick_at == sc.state.reconcile_tick_at


def test_a_repeated_tick_at_the_same_time_counts_once() -> None:
    sc = Scenario(2)
    sc.step()
    sc.set_valve_actual(1, OutputState.UNAVAILABLE)
    sc.advance(1)
    sc.step()  # same `now`
    sc.step()
    assert sc.state.zones["zone_1"].valve_output.mismatch_count == 1


def test_inactive_in_shadow_mode() -> None:
    sc = Scenario(2)
    sc.step()
    sc.set_valve_actual(1, OutputState.UNAVAILABLE)
    sc.advance(3)
    assert len(sc.events_of(MISMATCH)) == 1
    sc.shadow_mode = True
    sc.advance(10)
    assert sc.state.zones["zone_1"].valve_output == OutputTracking()  # reset silently
    assert sc.state.heat_source_output == OutputTracking()
    assert sc.events_of(MISMATCH_RECOVERED) == []
    assert len(sc.events_of(MISMATCH)) == 1

    sc.shadow_mode = False  # still unavailable: a fresh count from here
    sc.advance(2)
    assert len(sc.events_of(MISMATCH)) == 1
    sc.advance(1)
    assert len(sc.events_of(MISMATCH)) == 2


def test_heat_source_mismatch_message() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.set_hp_actual(OutputState.OFF)  # ignores the ON command
    sc.step()
    sc.advance(3)
    [alert] = sc.events_of(MISMATCH)
    assert alert.zone_id is None
    assert alert.message == (
        "The heat source switch does not follow its command: it should be ON but is OFF "
        "(3 reconcile intervals)."
    )
    assert alert.data == {"output": "heat_source", "desired": True, "actual": "off"}
    sc.set_hp_actual(OutputState.ON, follows=True)
    sc.advance(1)
    [recovered] = sc.events_of(MISMATCH_RECOVERED)
    assert recovered.message == "The heat source switch follows its command again."
    assert recovered.zone_id is None
    assert recovered.data == {"output": "heat_source"}


def test_mismatch_is_counted_outside_the_season() -> None:
    sc = Scenario(2)
    sc.set_season(False)
    sc.step()
    sc.set_valve_actual(2, OutputState.ON)  # e.g. switched in the Shelly app
    sc.advance(3)
    assert [e.zone_id for e in sc.events_of(MISMATCH)] == ["zone_2"]


# ---------------------------------------------------------------- active alerts


def test_no_active_alerts_normally() -> None:
    sc = Scenario(2)
    sc.step()
    assert active_alerts(sc.config, sc.state) == []


def test_active_alerts_list_faults_and_mismatches() -> None:
    sc = _faulty_from_0701(3)
    sc.set_valve_actual(2, OutputState.UNAVAILABLE)
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(3)
    alerts = active_alerts(sc.config, sc.state)
    assert [(a.kind, a.zone_id, a.data.get("output")) for a in alerts] == [
        (EventKind.OUTPUT_MISMATCH, None, "heat_source"),
        (STARTED, "zone_1", None),
        (EventKind.OUTPUT_MISMATCH, "zone_2", "valve"),
    ]
    assert alerts[0].message == "The heat source switch does not follow its command."
    assert alerts[1].message == "Sensor fault in Zone 1."
    assert alerts[1].data == {"since": at("07:01").isoformat()}
    assert alerts[2].message == "The valve of Zone 2 does not follow its command."

    sc.temp(1, 22.0)
    sc.set_valve_actual(2, OutputState.OFF, follows=True)
    sc.set_hp_actual(OutputState.OFF, follows=True)
    sc.advance(1)
    assert active_alerts(sc.config, sc.state) == []


def test_active_alerts_ignore_state_of_unknown_zones() -> None:
    config = make_config(1)
    assert active_alerts(config, CoreState()) == []


# ---------------------------------------------------------------- long run alarm


def _long_run(**kwargs: object) -> Scenario:
    """Zone 1 stays cold, so the heat source runs from 06:30 on."""
    sc = Scenario(2, temps={1: 20.0}, **kwargs)  # type: ignore[arg-type]
    sc.step()
    sc.advance_to("06:30")
    assert sc.hp
    return sc


def test_long_run_alarm_once_then_back_to_normal() -> None:
    sc = _long_run()
    sc.advance_to("18:30")
    assert sc.events_of(EventKind.LONG_RUN) == []  # exactly 12 h: not more yet
    sc.advance(1)
    [alarm] = sc.events_of(EventKind.LONG_RUN)
    assert alarm.message == "The heat source has been running for more than 12 h (since Mon 06:30)."
    assert alarm.data == {"on_since": at("06:30").isoformat()}
    assert [a.kind for a in active_alerts(sc.config, sc.state)] == [EventKind.LONG_RUN]
    sc.advance_to("20:00")
    assert len(sc.events_of(EventKind.LONG_RUN)) == 1
    sc.temp(1, 22.5)
    sc.temp(2, 22.5)  # warm: no sync join
    sc.step()
    assert not sc.hp
    [ended] = sc.events_of(EventKind.LONG_RUN_ENDED)
    assert ended.message == "The heat source is OFF again after its long run."
    assert active_alerts(sc.config, sc.state) == []


def test_long_run_alarm_uses_its_setting_and_local_time() -> None:
    params = GlobalParams(long_run_alarm=timedelta(hours=2))
    sc = _long_run(params=params, tz=ZoneInfo("Europe/Budapest"))
    sc.advance_to("08:31")
    [alarm] = sc.events_of(EventKind.LONG_RUN)
    assert "more than 2 h (since Mon 06:30)" in alarm.message


def test_long_run_alarm_survives_an_unavailable_spell() -> None:
    """Unavailable while ON ends nothing; back ON, it never stopped; back OFF, the
    run is over."""
    sc = _long_run()
    sc.advance_to("18:31")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(5)
    assert sc.events_of(EventKind.LONG_RUN_ENDED) == []
    assert sc.state.long_run_alerted
    sc.set_hp_actual(OutputState.ON, follows=True)
    sc.advance(5)
    assert sc.events_of(EventKind.LONG_RUN_ENDED) == []
    assert len(sc.events_of(EventKind.LONG_RUN)) == 1
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(1)
    sc.set_hp_actual(OutputState.OFF)
    sc.step()
    assert len(sc.events_of(EventKind.LONG_RUN_ENDED)) == 1


def test_long_run_alarm_in_shadow_mode_and_not_repeated_after_restart() -> None:
    sc = _long_run()
    sc.shadow_mode = True
    sc.advance_to("18:31")
    assert len(sc.events_of(EventKind.LONG_RUN)) == 1
    sc.restart(downtime=5)
    sc.step()
    assert len(sc.events_of(EventKind.LONG_RUN)) == 1
    assert sc.state.long_run_alerted


def test_long_run_ends_when_the_season_stops_it() -> None:
    sc = _long_run()
    sc.advance_to("18:31")
    sc.set_season(False)
    sc.step()
    assert len(sc.events_of(EventKind.LONG_RUN_ENDED)) == 1
