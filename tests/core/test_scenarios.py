"""§6 acceptance scenarios covered by the core in P2 (docs/design.md §6).

Defaults from §4: SetPoint 22.0, Hysteresis 0.2 (StartTemp 21.8, StopTemp 22.2),
WaitTime 30 min, HpMinOnTime/HpMinOffTime 60 min, SensorFaultTimeout 60 min,
ManualMaxTemp 25.
"""

from __future__ import annotations

from collections.abc import Mapping

from custom_components.floorheat.core.io import OutputState
from custom_components.floorheat.core.state import ZoneMode

from .harness import Scenario

IDLE, WAITING, HEATING, FAULT = (
    ZoneMode.IDLE,
    ZoneMode.WAITING,
    ZoneMode.HEATING,
    ZoneMode.SENSOR_FAULT,
)


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
    assert sc.reason(1) == "Waiting, 30 min left"

    sc.advance_to("06:29")
    assert sc.mode(1) is WAITING
    assert not sc.hp
    assert sc.reason(1) == "Waiting, 1 min left"

    sc.advance_to("06:30")
    assert sc.mode(1) is HEATING
    assert sc.hp
    assert sc.valve(1) is True
    assert sc.calling_zone == "zone_1"
    assert sc.reason(1) == "Calling zone"
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
    assert sc.reason(2) == "Heating"
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
    assert sc.reason(2) == "Spreading heat (min ON), 20 min left"
    assert sc.reason(4) == "Idle, at or above ManualMaxTemp"

    sc.advance_to("07:29")
    assert sc.hp
    sc.advance_to("07:30")
    assert not sc.hp
    assert sc.open_valves() == set()
    assert sc.reason(2) == "Idle"


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
    assert sc.reason(2) == "Held by min OFF, 20 min left"

    sc.advance_to("08:59")
    assert not sc.hp
    sc.advance_to("09:00")
    assert sc.hp
    assert sc.calling_zone == "zone_2"


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
    assert sc.reason(1) == "Sensor fault, following the heat pump"

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
    assert sc.reason(1) == "Waiting, 7 min left"
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
    assert sc.reason(1) == "Heating, heat source unavailable"
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
