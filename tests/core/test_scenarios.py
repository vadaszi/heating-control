"""§6 acceptance scenarios covered by the core in P2, P3 and P9 (docs/design.md §6).

Defaults from §4: SetPoint 22.0, Hysteresis 0.2 (StartTemp 21.8, StopTemp 22.2),
WaitTime 30 min, HpMinOnTime/HpMinOffTime 60 min, SensorFaultTimeout 60 min,
ManualMaxTemp 25.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.multizone_floor_heating_manager.core.config import CoreConfig
from custom_components.multizone_floor_heating_manager.core.io import (
    EventKind,
    HeatSourceStatus,
    OutputState,
    Reason,
)
from custom_components.multizone_floor_heating_manager.core.schedule import (
    WEEKDAYS,
    Schedule,
    ScheduleKind,
    check_new_schedule,
)
from custom_components.multizone_floor_heating_manager.core.state import ZoneMode

from .harness import DAY, Scenario, at

IDLE, WAITING, HEATING, FORCED, FAULT = (
    ZoneMode.IDLE,
    ZoneMode.WAITING,
    ZoneMode.HEATING,
    ZoneMode.FORCED,
    ZoneMode.SENSOR_FAULT,
)
NEXT_DAY = DAY + timedelta(days=1)


def _started_by_zone_1(
    zones: int = 3, others: Mapping[int | str, float | None] | None = None
) -> Scenario:
    """HP off; zone 1 drops to StartTemp at 06:00; the HP starts at 06:30."""
    sc = Scenario(zones, temps={1: 21.8, **(others or {})})
    sc.step()
    sc.advance_to("06:30")
    assert sc.hp
    assert sc.calling_zone == "zone_1"
    return sc


def test_a01_wait_then_start() -> None:
    sc = Scenario(3, temps={1: 22.0})
    sc.step()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE}
    sc.temp(1, 21.8)
    sc.step()
    assert sc.mode(1) is WAITING
    assert not sc.hp
    assert sc.valve(1) is False
    assert sc.reason(1) == Reason.WAITING
    assert sc.until(1) == at("06:30")

    sc.advance_to("06:29")
    assert sc.mode(1) is WAITING
    assert not sc.hp
    assert sc.reason(1) == Reason.WAITING  # fixed text, no countdown (D-123)
    assert sc.until(1) == at("06:30")

    sc.advance_to("06:30")
    assert sc.mode(1) is HEATING
    assert sc.hp
    assert sc.valve(1) is True
    assert sc.calling_zone == "zone_1"
    assert sc.reason(1) == Reason.CALLING_ZONE
    assert sc.until(1) is None
    assert sc.state.hp_last_on_at == sc.now


def test_a02_window_closed_before_expiry() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:10")
    sc.temp(1, 22.0)
    sc.advance_to("06:30")
    assert sc.mode(1) is IDLE
    assert not sc.hp
    sc.advance_to("08:00")
    assert not sc.hp


def test_a03_only_the_expiry_check_counts() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:10")
    sc.temp(1, 22.1)  # above StartTemp during the wait
    sc.advance_to("06:20")
    assert sc.mode(1) is WAITING  # readings during the wait are ignored (D-05)
    sc.temp(1, 21.7)
    sc.advance_to("06:30")
    assert sc.mode(1) is HEATING
    assert sc.hp


def test_a04_join_while_running_without_wait() -> None:
    sc = _started_by_zone_1()
    sc.advance_to("06:40")
    sc.temp(2, 21.8)
    sc.step()
    assert sc.mode(2) is HEATING
    assert sc.valve(2) is True
    assert sc.reason(2) == Reason.HEATING
    assert sc.calling_zone == "zone_1"


def test_a05_waiting_zone_joins_when_the_hp_starts() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:10")
    sc.temp(2, 21.8)
    sc.step()
    assert sc.mode(2) is WAITING
    sc.advance_to("06:30")
    assert sc.hp
    assert sc.mode(2) is HEATING  # joins in the same reconcile, 10 min before its wait ends
    assert sc.calling_zone == "zone_1"


def test_a06_sync_rule_fires_once_per_cycle() -> None:
    sc = _started_by_zone_1(3, {2: 22.2})
    sc.temp(3, 21.9)  # SetPoint - 0.1, above StartTemp: no demand of its own
    sc.advance_to("07:00")
    assert sc.mode(3) is IDLE
    assert not sc.sync_fired

    sc.temp(1, 22.0)  # the calling zone reaches SetPoint
    sc.step()
    assert sc.sync_fired
    assert sc.mode(3) is HEATING
    assert sc.mode(2) is IDLE  # at StopTemp: not joined
    assert sc.mode(1) is HEATING  # the calling zone heats on to StopTemp

    sc.temp(2, 21.9)  # zone 2 cools, still above StartTemp
    sc.temp(1, 21.9)
    sc.advance(5)
    sc.temp(1, 22.0)  # the calling zone reaches SetPoint again
    sc.advance(5)
    assert sc.mode(2) is IDLE  # no second sync in this cycle


def test_a07_joined_zone_stops_at_stoptemp_hp_stays_on() -> None:
    sc = _started_by_zone_1(3, {2: 22.2})
    sc.temp(3, 21.9)
    sc.advance_to("07:00")
    sc.temp(1, 22.0)
    sc.step()
    assert sc.mode(3) is HEATING

    sc.advance_to("07:10")
    sc.temp(3, 22.2)
    sc.step()
    assert sc.mode(3) is IDLE
    assert sc.valve(3) is False
    assert sc.hp  # zone 1 still heats

    sc.advance_to("07:40")
    sc.temp(1, 22.2)
    sc.step()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE}
    assert not sc.hp  # min ON (06:30 + 60) has elapsed
    assert sc.calling_zone is None
    assert not sc.sync_fired


def test_a08_all_satisfied_before_min_on_spreads_heat() -> None:
    sc = _started_by_zone_1(4, {2: 22.2, 3: 22.2, 4: 25.0})
    sc.advance_to("07:10")  # 40 min after HP ON
    sc.temp(1, 22.2)
    sc.step()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE, "zone_4": IDLE}
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_2", "zone_3"}  # zone 4 >= ManualMaxTemp
    assert sc.reason(2) == Reason.SPREADING_HEAT
    assert sc.until(2) == at("07:30")
    assert sc.reason(4) == Reason.TOO_WARM_FOR_SPREADING
    assert sc.until(4) is None

    sc.advance_to("07:29")
    assert sc.hp
    sc.advance_to("07:30")
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.reason(2) == Reason.IDLE
    assert sc.until(2) is None


def _hp_off_at_0800() -> Scenario:
    """First start at 07:00 with the heat source ON and no demand: min ON counts from
    startup (D-91), so the request goes OFF at 08:00."""
    sc = Scenario(3, start="07:00", hp_on=True)
    sc.step()
    assert sc.hp
    sc.advance_to("07:59")
    assert sc.hp
    sc.advance_to("08:00")
    assert not sc.hp
    assert sc.state.hp_last_off_at == sc.now
    return sc


def test_a09_demand_held_back_by_min_off() -> None:
    sc = _hp_off_at_0800()
    sc.advance_to("08:10")
    sc.temp(2, 21.8)
    sc.step()
    assert sc.mode(2) is WAITING

    sc.advance_to("08:39")
    assert sc.mode(2) is WAITING
    assert sc.valve(2) is False
    sc.advance_to("08:40")
    assert sc.mode(2) is HEATING
    assert sc.valve(2) is True  # valve opens at the end of the wait (D-64)
    assert not sc.hp
    assert sc.calling_zone is None
    assert sc.reason(2) == Reason.HELD_BY_MIN_OFF
    assert sc.until(2) == at("09:00")

    sc.advance_to("08:59")
    assert not sc.hp
    sc.advance_to("09:00")
    assert sc.hp
    assert sc.calling_zone == "zone_2"


# ---------------------------------------------------------------- heat source status (D-141)


def test_heat_source_status_through_a_cycle() -> None:
    """Idle -> heating -> spreading heat (until min ON end) -> idle."""
    sc = Scenario(2, temps={1: 22.0, 2: 22.2})  # zone 2 at StopTemp: no sync join
    sc.step()
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.IDLE, None)
    sc.temp(1, 21.8)
    sc.step()
    assert sc.source_status == HeatSourceStatus.IDLE  # waiting: no demand yet
    sc.advance_to("06:30")
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.HEATING, None)
    sc.advance_to("07:10")
    sc.temp(1, 22.2)
    sc.step()
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.SPREADING_HEAT, at("07:30"))
    sc.advance_to("07:30")
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.IDLE, None)


def test_heat_source_status_held_by_min_off() -> None:
    sc = _hp_off_at_0800()
    sc.advance_to("08:10")
    sc.temp(2, 21.8)
    sc.step()
    sc.advance_to("08:40")
    assert (sc.source_status, sc.source_until) == (
        HeatSourceStatus.HELD_BY_MIN_OFF,
        at("09:00"),
    )
    sc.advance_to("09:00")
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.HEATING, None)


def test_heat_source_status_unavailable_comes_first() -> None:
    sc = _started_by_zone_1(2)
    sc.set_season(False)
    sc.step()
    assert sc.source_status == HeatSourceStatus.SEASON_OFF
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    assert (sc.source_status, sc.source_until) == (HeatSourceStatus.UNAVAILABLE, None)


def test_a09_stoptemp_reached_while_held() -> None:
    sc = _hp_off_at_0800()
    sc.advance_to("08:10")
    sc.temp(2, 21.8)
    sc.step()
    sc.advance_to("08:40")
    assert sc.mode(2) is HEATING
    sc.advance_to("08:50")
    sc.temp(2, 22.2)
    sc.step()
    assert sc.mode(2) is IDLE
    assert sc.valve(2) is False
    sc.advance_to("10:00")
    assert not sc.hp


def test_a18_calling_zone_sensor_fails_mid_cycle() -> None:
    sc = _started_by_zone_1(3, {2: 21.9, 3: 22.2})
    sc.advance_to("06:40")
    sc.temp(1, 21.9)
    sc.step()
    sc.silence(1)  # last valid reading 21.9 at 06:40
    sc.advance_to("07:40")
    assert sc.mode(1) is HEATING  # exactly SensorFaultTimeout old: still valid
    assert not sc.sync_fired

    sc.advance_to("07:41")
    assert sc.mode(1) is FAULT
    assert sc.state.zones["zone_1"].fault_since == sc.now
    assert sc.sync_fired  # a faulty calling zone counts as having reached SetPoint (D-28)
    assert sc.mode(2) is HEATING
    assert sc.mode(3) is IDLE  # at StopTemp
    assert sc.hp
    assert sc.valve(1) is True  # follows the house while the HP runs
    assert sc.room_temp(1) is None
    assert sc.reason(1) == Reason.SENSOR_FAULT

    sc.temp(2, 22.2)
    sc.step()
    assert not sc.hp  # the faulty zone creates no demand; min ON has elapsed
    assert sc.valve(1) is False


def test_a22_restart_during_wait_continues_the_wait() -> None:
    """Core part of A22: the logic state survives persistence (the HA side is P5)."""
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:20")  # 10 min left
    sc.restart(downtime=3)
    sc.step()
    assert sc.mode(1) is WAITING
    assert sc.reason(1) == Reason.WAITING
    assert sc.until(1) == at("06:30")  # the original end of the wait
    sc.advance_to("06:29")
    assert not sc.hp
    sc.advance_to("06:30")
    assert sc.hp


def test_a22_restart_keeps_min_on_from_the_original_start() -> None:
    sc = _started_by_zone_1(2, {2: 22.2})
    sc.advance_to("06:50")  # HP ON for 20 min
    sc.restart(downtime=5)
    sc.step()
    assert sc.state.hp_last_on_at == sc.now.replace(hour=6, minute=30)
    assert sc.calling_zone == "zone_1"
    sc.advance_to("07:00")
    sc.temp(1, 22.2)
    sc.step()
    assert sc.hp  # min ON counts from 06:30, not from the restart
    sc.advance_to("07:30")
    assert not sc.hp


def test_a23_unvalved_zone_can_start_the_hp() -> None:
    sc = Scenario(2, unvalved=[2], temps={2: 21.8})
    sc.step()
    assert sc.mode(2) is WAITING
    sc.advance_to("06:30")
    assert sc.mode(2) is HEATING
    assert sc.hp
    assert sc.calling_zone == "zone_2"
    assert sc.valve(2) is None  # no output at all
    assert sc.open_valves() == set()


def test_a26_largest_deficit_becomes_the_calling_zone() -> None:
    sc = Scenario(3, temps={1: 21.7, 2: 21.5})
    sc.step()
    sc.advance_to("06:30")
    assert sc.mode(1) is HEATING
    assert sc.mode(2) is HEATING
    assert sc.calling_zone == "zone_2"


def test_a26_equal_deficits_go_to_yaml_order() -> None:
    sc = Scenario(3, temps={2: 21.6, 3: 21.6})
    sc.step()
    sc.advance_to("06:30")
    assert sc.calling_zone == "zone_2"


def test_a29_heat_source_unavailable_counts_as_off() -> None:
    """Core part of A29 (the mismatch alert is P3), with D-95: while unavailable the
    heat source counts as OFF, min OFF counts from when it became unavailable, and the
    cycle is kept until the switch reports again."""
    sc = _started_by_zone_1(3)
    sc.advance_to("06:45")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    assert sc.state.hp_unavailable_since == sc.now
    assert sc.state.hp_actual_on is True  # last known state
    assert not sc.hp  # held by min OFF, counted from 06:45 (the command cannot be sent)
    assert sc.mode(1) is HEATING
    assert sc.valve(1) is True
    assert sc.reason(1) == Reason.HEAT_SOURCE_UNAVAILABLE
    assert sc.calling_zone == "zone_1"  # the cycle is kept while unknown (D-95)

    sc.advance_to("06:50")
    sc.temp(2, 21.7)
    sc.step()
    assert sc.mode(2) is WAITING  # no join by rule 3: the HP does not count as running

    sc.advance_to("07:44")
    assert not sc.hp
    sc.advance_to("07:45")
    assert sc.hp  # min OFF (from 06:45) elapsed; the request is repeated
    assert sc.calling_zone == "zone_1"


def test_a30_first_start_without_persisted_state() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.valves_actual["zone_1"] = OutputState.ON  # read back as found
    sc.step()
    assert sc.state.hp_last_off_at is None  # D-78: no min OFF
    assert sc.mode(1) is WAITING
    assert sc.valve(1) is False  # corrected to the desired state
    sc.advance_to("06:30")
    assert sc.hp
    assert sc.calling_zone == "zone_1"


def test_heat_source_wifi_glitch_no_off_after_it_returns_on() -> None:
    """Owner requirement (D-95): a Wi-Fi glitch or router restart must not turn a
    working, running heat pump OFF. Back ON means it never stopped: min ON, the calling
    zone and the sync flag carry on."""
    sc = _started_by_zone_1(3, {2: 21.9})
    sc.advance_to("06:40")
    sc.temp(1, 22.0)
    sc.step()
    assert sc.sync_fired
    sc.advance_to("06:45")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(3)
    assert not sc.hp  # the switch is unreachable: nothing can be sent anyway
    sc.set_hp_actual(OutputState.ON, follows=True)  # back, still ON: it never stopped
    sc.step()
    assert sc.hp
    assert sc.mode(1) is HEATING
    assert sc.state.hp_last_on_at == sc.now.replace(hour=6, minute=30)  # not restarted
    assert sc.state.hp_last_off_at is None
    assert sc.state.hp_unavailable_since is None
    assert sc.calling_zone == "zone_1"
    assert sc.sync_fired  # the same cycle: no second sync


def test_heat_source_wifi_glitch_does_not_restart_min_on() -> None:
    """Back ON within min ON and nothing needs heat: the D-20 spread ends at the
    original min ON (07:30), not 60 min after the glitch."""
    sc = _started_by_zone_1(2, {2: 22.2})
    sc.advance_to("07:10")
    sc.temp(1, 22.2)
    sc.step()
    assert sc.hp  # spread until 07:30
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(5)
    sc.set_hp_actual(OutputState.ON, follows=True)
    sc.step()
    assert sc.hp
    sc.advance_to("07:29")
    assert sc.hp
    sc.advance_to("07:30")
    assert not sc.hp


def test_heat_source_back_off_after_power_loss() -> None:
    """D-95: back OFF after being ON means it really stopped (a Shelly restarts OFF),
    counted from when it became unavailable; the cycle has ended."""
    sc = _started_by_zone_1(3)
    sc.advance_to("06:45")
    unavailable_at = sc.now
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    sc.advance(5)
    sc.temp(2, 21.7)
    sc.step()
    sc.set_hp_actual(OutputState.OFF, follows=True)
    sc.step()
    assert sc.state.hp_last_off_at == unavailable_at
    assert sc.state.hp_actual_on is False
    assert sc.calling_zone is None  # held by min OFF: a new cycle follows
    assert not sc.hp
    sc.advance_to("07:44")
    assert not sc.hp
    sc.advance_to("07:45")
    assert sc.hp
    assert sc.calling_zone == "zone_2"  # new cycle: zone 2 has the larger deficit


def test_a20_season_off_mid_cycle_ignores_min_on() -> None:
    """A20 (season part): switched OFF 20 min into a cycle, the request goes OFF and
    every valve closes at once, although min ON has not elapsed (D-68, D-97)."""
    sc = _started_by_zone_1(3, {2: 21.8})
    sc.advance_to("06:50")
    assert sc.open_valves() == {"zone_1", "zone_2"}
    sc.set_season(False)
    sc.step()
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE}
    assert sc.calling_zone is None
    assert not sc.sync_fired
    assert sc.reason(1) == Reason.SEASON_OFF
    assert sc.state.hp_last_off_at == sc.now  # min OFF counts from the actual OFF


def test_a20_no_demand_while_season_off() -> None:
    sc = Scenario(3, temps={1: 21.0})
    sc.set_season(False)
    sc.step()
    assert sc.mode(1) is IDLE
    sc.temp(2, 18.0)
    sc.advance_to("12:00")
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE}

    sc.set_season(True)  # back ON: normal logic from IDLE, with WaitTime (rule 1)
    sc.step()
    assert sc.mode(1) is WAITING
    assert sc.mode(2) is WAITING
    assert not sc.hp
    sc.advance_to("12:30")
    assert sc.hp
    assert sc.calling_zone == "zone_2"  # largest deficit (D-65)


def test_a17_sensor_fault_notified_and_reminded_next_day() -> None:
    sc = Scenario(4, temps={2: 22.2, 3: 22.2})  # at StopTemp: no sync join
    sc.step()
    sc.silence(4)  # silent from 06:00
    sc.advance_to("07:00")
    assert sc.mode(4) is IDLE  # exactly SensorFaultTimeout: still valid
    sc.advance_to("07:01")
    assert sc.mode(4) is FAULT
    assert [e.zone_id for e in sc.events_of(EventKind.SENSOR_FAULT_STARTED)] == ["zone_4"]
    assert not sc.hp  # no demand
    assert sc.valve(4) is False  # the HP is off

    sc.temp(1, 21.8)  # zone 1 starts the HP at 07:31 (first start: no min OFF)
    sc.step()
    sc.advance_to("07:31")
    assert sc.hp
    assert sc.valve(4) is True  # follows the house
    sc.temp(1, 22.2)
    sc.advance_to("08:31")  # min ON elapsed
    assert not sc.hp
    assert sc.valve(4) is False

    sc.advance_to("07:59", NEXT_DAY)
    assert sc.events_of(EventKind.SENSOR_FAULT_REMINDER) == []
    sc.advance_to("08:00", NEXT_DAY)
    [reminder] = sc.events_of(EventKind.SENSOR_FAULT_REMINDER)
    assert reminder.data == {"zone_ids": "zone_4"}
    assert len(sc.events_of(EventKind.SENSOR_FAULT_STARTED)) == 1

    sc.temp(4, 22.0)
    sc.step()
    assert sc.mode(4) is IDLE
    assert [e.zone_id for e in sc.events_of(EventKind.SENSOR_FAULT_RECOVERED)] == ["zone_4"]


def test_a20_fault_notified_without_reminder() -> None:
    """A20: outside the season a sensor fault is notified, with no daily reminder (D-75)."""
    sc = Scenario(2)
    sc.set_season(False)
    sc.step()
    sc.silence(1)
    sc.advance_to("07:01")
    assert sc.mode(1) is FAULT
    assert sc.reason(1) == Reason.SENSOR_FAULT_SEASON_OFF
    assert len(sc.events_of(EventKind.SENSOR_FAULT_STARTED)) == 1
    sc.advance_to("12:00", NEXT_DAY)
    assert sc.events_of(EventKind.SENSOR_FAULT_REMINDER) == []
    sc.temp(1, 22.0)
    sc.step()
    assert len(sc.events_of(EventKind.SENSOR_FAULT_RECOVERED)) == 1


def test_a27_unavailable_valve_alerts_once_then_recovery() -> None:
    """A27 (logic part): the retry backoff is the adapter's job (P5)."""
    sc = Scenario(2)
    sc.step()
    sc.set_valve_actual(1, OutputState.UNAVAILABLE)
    sc.advance(2)
    assert sc.events_of(EventKind.OUTPUT_MISMATCH) == []
    sc.advance(1)
    [alert] = sc.events_of(EventKind.OUTPUT_MISMATCH)
    assert alert.zone_id == "zone_1"
    assert alert.data == {"output": "valve", "desired": False, "actual": "unavailable"}
    sc.advance(30)
    assert len(sc.events_of(EventKind.OUTPUT_MISMATCH)) == 1  # notified once

    sc.set_valve_actual(1, OutputState.OFF, follows=True)
    sc.advance(1)
    [recovered] = sc.events_of(EventKind.OUTPUT_MISMATCH_RECOVERED)
    assert recovered.zone_id == "zone_1"
    assert recovered.message == "The valve of Zone 1 follows its command again."
    assert recovered.data == {"output": "valve"}
    sc.advance(10)
    assert len(sc.events_of(EventKind.OUTPUT_MISMATCH_RECOVERED)) == 1


def test_a27_valve_ignoring_commands_alerts_once() -> None:
    sc = _started_by_zone_1(2)
    sc.temp(2, 21.8)
    sc.set_valve_actual(2, OutputState.OFF)  # stays OFF whatever is commanded
    sc.advance(1)
    assert sc.valve(2) is True
    sc.advance(2)
    assert sc.events_of(EventKind.OUTPUT_MISMATCH) == []
    sc.advance(1)
    assert [e.zone_id for e in sc.events_of(EventKind.OUTPUT_MISMATCH)] == ["zone_2"]
    sc.advance(20)
    assert len(sc.events_of(EventKind.OUTPUT_MISMATCH)) == 1


def test_a29_heat_source_unavailable_alerts_mismatch() -> None:
    """A29 (alert part): unavailable counts as a mismatch although the cycle is kept
    (D-67, D-95); back ON is a recovery."""
    sc = _started_by_zone_1(3)
    sc.advance_to("06:45")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(2)
    assert sc.events_of(EventKind.OUTPUT_MISMATCH) == []
    sc.advance(1)
    [alert] = sc.events_of(EventKind.OUTPUT_MISMATCH)
    assert alert.zone_id is None
    assert alert.data["output"] == "heat_source"
    assert alert.data["actual"] == "unavailable"
    assert sc.calling_zone == "zone_1"  # the cycle is kept (D-95)

    sc.set_hp_actual(OutputState.ON, follows=True)  # back ON: it never stopped
    sc.advance(1)
    assert sc.hp
    assert len(sc.events_of(EventKind.OUTPUT_MISMATCH_RECOVERED)) == 1


# ---------------------------------------------------------------- P9: schedules and holiday


def test_a10_auto_schedule_raise_starts_without_wait() -> None:
    sc = Scenario(2, temps={1: 22.1}, start="12:00")
    sc.add_auto([1], "13:00", "17:00", 23.0)
    sc.step()
    sc.advance_to("12:59")
    assert sc.mode(1) is IDLE
    assert sc.setpoint(1) == 22.0
    sc.advance_to("13:00")
    assert sc.setpoint(1) == 23.0
    assert sc.mode(1) is HEATING  # no WaitTime (D-26)
    assert sc.hp
    assert sc.calling_zone == "zone_1"
    assert sc.reason(1) == Reason.CALLING_ZONE


def test_a10_auto_schedule_raise_is_held_by_min_off() -> None:
    sc = Scenario(2, temps={1: 22.1}, start="11:30", hp_on=True)  # min ON from startup
    sc.add_auto([1], "13:00", "17:00", 23.0)
    sc.step()
    sc.advance_to("12:30")
    assert not sc.hp  # OFF at 12:30: min OFF until 13:30
    sc.advance_to("13:00")
    assert sc.mode(1) is HEATING
    assert sc.valve(1) is True  # open while held (D-64)
    assert not sc.hp
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF
    assert sc.until(1) == at("13:30")
    sc.advance_to("13:30")
    assert sc.hp
    assert sc.calling_zone == "zone_1"


def test_a11_auto_schedule_end_stops_the_zone() -> None:
    sc = Scenario(2, temps={1: 22.5, 2: 22.3}, start="12:59")  # zone 2 above StopTemp
    sc.add_auto([1], "13:00", "17:00", 23.0)
    sc.step()
    sc.advance_to("13:00")
    assert sc.mode(1) is HEATING
    sc.advance_to("16:59")
    assert sc.mode(1) is HEATING
    assert sc.hp
    sc.advance_to("17:00")
    assert sc.setpoint(1) == 22.0
    assert sc.mode(1) is IDLE  # 22.5 >= StopTemp 22.2 (rule 6)
    assert not sc.hp  # min ON (from 13:00) has elapsed


def test_a12_overlapping_auto_schedule_is_rejected() -> None:
    config = CoreConfig(zones=Scenario(2).config.zones)
    existing = Schedule(
        id="daily",
        kind=ScheduleKind.AUTO,
        start=time(13, 0),
        end=time(17, 0),
        zone_ids=("zone_1",),
        weekdays=WEEKDAYS,
        temperature=23.0,
    )
    new = Schedule(
        id="sunday",
        kind=ScheduleKind.AUTO,
        start=time(16, 0),
        end=time(18, 0),
        zone_ids=("zone_1", "zone_2"),
        weekdays=frozenset({6}),
        temperature=21.0,
    )
    stored = [existing]
    errors = check_new_schedule(new, stored, config, at("12:00"), UTC)
    assert errors == [
        "overlaps auto schedule 'daily' for zone_1; auto schedules for the same zone may "
        "not overlap"
    ]
    assert stored == [existing]  # nothing stored (the adapter only stores without errors)


def _manual_zone_2(zones: int = 3, temps: Mapping[int | str, float] | None = None) -> Scenario:
    """All zones satisfied; a manual schedule forces zone 2 from 04:00 to 06:00."""
    sc = Scenario(zones, temps={**(temps or {})}, start="03:30")
    sc.add_manual([2], "04:00", "06:00")
    sc.step()
    assert not sc.hp
    sc.advance_to("04:00")
    return sc


def test_a13_manual_schedule_forces_the_zone() -> None:
    sc = _manual_zone_2()
    assert sc.modes() == {"zone_1": IDLE, "zone_2": FORCED, "zone_3": IDLE}
    assert sc.hp
    assert sc.open_valves() == {"zone_2"}
    assert sc.calling_zone is None  # a forced zone never calls (D-44)
    assert sc.reason(2) == Reason.FORCED
    assert sc.until(2) == at("06:00")
    assert sc.setpoint(2) == 22.0  # the SetPoint below the manual schedule (D-130)
    sc.advance_to("05:59")
    assert sc.hp
    assert sc.mode(2) is FORCED
    sc.advance_to("06:00")
    assert sc.mode(2) is IDLE
    assert not sc.hp  # min ON (from 04:00) is satisfied
    assert sc.open_valves() == set()
    assert sc.schedules == []  # the one-shot schedule was deleted after its window


def test_a14_zone_joining_a_manual_cycle_becomes_the_calling_zone() -> None:
    sc = _manual_zone_2(temps={1: 22.1})
    sc.advance_to("05:00")
    sc.temp(3, 21.8)
    sc.step()
    assert sc.mode(3) is HEATING  # joins without wait (rule 3)
    assert sc.calling_zone == "zone_3"  # D-44
    assert not sc.sync_fired
    sc.advance_to("05:30")
    sc.temp(3, 22.0)  # reaches SetPoint
    sc.step()
    assert sc.sync_fired
    assert sc.mode(1) is HEATING  # 22.1 < StopTemp: tops up
    assert sc.mode(2) is FORCED


def test_a15_forced_zone_capped_at_manual_max_temp() -> None:
    sc = Scenario(2, temps={1: 24.9}, start="04:00")
    sc.add_manual([1], "04:00", "08:00")
    sc.step()
    assert sc.mode(1) is FORCED
    assert sc.valve(1) is True
    assert sc.hp
    sc.advance_to("04:10")
    sc.temp(1, 25.0)
    sc.step()
    assert sc.mode(1) is FORCED
    assert sc.capped(1)
    assert sc.valve(1) is False  # closed, no demand
    assert sc.reason(1) == Reason.FORCED_TOO_WARM
    assert sc.until(1) is None
    assert sc.hp  # min ON (from 04:00): the heat is spread over zone 2
    assert sc.open_valves() == {"zone_2"}
    sc.advance_to("04:20")
    sc.temp(1, 24.5)  # below ManualMaxTemp but not below the resume limit
    sc.step()
    assert sc.capped(1)
    assert sc.valve(1) is False  # the cap wins over the spread (D-130)
    sc.advance_to("05:00")
    assert not sc.hp  # no demand once min ON has elapsed
    sc.temp(1, 24.0)
    sc.advance(1)
    assert sc.capped(1)  # resumes only below 24.0
    sc.temp(1, 23.9)
    sc.advance_to("05:30")
    assert not sc.capped(1)
    assert sc.valve(1) is True
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF  # min OFF from 05:00
    sc.advance_to("06:00")
    assert sc.hp
    assert sc.reason(1) == Reason.FORCED


def test_a16_holiday_until_sunday_1500() -> None:
    sunday = date(2026, 1, 18)
    sc = Scenario(3, temps={1: 20.0, 2: 20.0, 3: 19.5}, day=sunday, start="12:00")
    sc.holiday("15:00")
    sc.add_auto([1], "10:00", "16:00", 23.0)
    sc.add_manual([2], "12:00", "16:00")
    sc.step()
    assert [sc.setpoint(z) for z in (1, 2, 3)] == [18.0, 18.0, 18.0]
    assert sc.modes() == {"zone_1": IDLE, "zone_2": IDLE, "zone_3": IDLE}  # schedules ignored
    assert sc.holiday_active
    sc.advance_to("14:59")
    assert not sc.hp
    sc.advance_to("15:00")
    assert [sc.setpoint(z) for z in (1, 2, 3)] == [23.0, 22.0, 22.0]
    assert sc.modes() == {"zone_1": HEATING, "zone_2": FORCED, "zone_3": HEATING}  # no wait
    assert sc.hp
    assert sc.calling_zone == "zone_1"  # largest deficit (2.8 vs 2.3)
    assert not sc.holiday_active
    assert sc.holiday_until is None  # switched off by the adapter


BERLIN = ZoneInfo("Europe/Berlin")


def _local_setpoints(sc: Scenario, until: datetime) -> dict[str, float]:
    """Effective SetPoint of zone 1 per local time, minute by minute."""
    seen: dict[str, float] = {}
    while sc.now < until:
        sc.advance(1)
        seen[sc.local_now().strftime("%H:%M%z")] = sc.setpoint(1)
    return seen


def test_a24_night_window_across_the_spring_dst_change() -> None:
    sc = Scenario(1, tz=BERLIN, day=date(2026, 3, 28), start="21:00", temps=22.5)
    sc.add_auto([1], "22:00", "02:00", 23.0, weekdays=WEEKDAYS)
    sc.step()
    seen = _local_setpoints(sc, datetime(2026, 3, 29, 2, 0, tzinfo=UTC))  # 04:00 CEST
    assert seen["21:59+0100"] == 22.0
    assert seen["22:00+0100"] == 23.0
    assert seen["01:59+0100"] == 23.0
    assert seen["03:00+0200"] == 22.0  # 02:00 does not exist: the window ends at 03:00
    assert sum(1 for value in seen.values() if value == 23.0) == 4 * 60


def test_a24_night_window_across_the_autumn_dst_change() -> None:
    sc = Scenario(1, tz=BERLIN, day=date(2026, 10, 24), start="21:00", temps=22.5)
    sc.add_auto([1], "22:00", "02:00", 23.0, weekdays=WEEKDAYS)
    sc.step()
    seen = _local_setpoints(sc, datetime(2026, 10, 25, 3, 0, tzinfo=UTC))  # 04:00 CET
    assert seen["22:00+0200"] == 23.0
    assert seen["01:59+0200"] == 23.0
    assert seen["02:00+0200"] == 22.0  # the first 02:00 ends it
    assert seen["02:30+0100"] == 22.0  # the repeated hour stays outside
    assert sum(1 for value in seen.values() if value == 23.0) == 4 * 60


def test_a28_manual_schedule_does_not_force_a_faulty_zone() -> None:
    sc = Scenario(2, start="03:00")
    sc.add_manual([2], "04:30", "06:00")
    sc.step()
    sc.silence(2)
    sc.advance_to("04:01")
    assert sc.mode(2) is FAULT
    sc.advance_to("04:30")
    assert sc.mode(2) is FAULT  # D-70
    assert sc.reason(2) == Reason.SENSOR_FAULT
    assert not sc.hp  # no demand from it
    assert sc.valve(2) is False  # follows the house
